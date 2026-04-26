"""
summarizer.py — Bilingual meeting summarizer (English + Urdu)

Improvements over v2:
• Multilingual prompts — handles English, Urdu, and code-switched audio
• Output language selection — English / Urdu / Both
• Code-switching aware — common in Pakistani meetings
• Richer schema: meeting_type, sentiment, risks, speaker_contributions, stats
• JSON validation + auto-repair pass
• Smarter token-aware chunking with overlap
• Action-item deduplication
• Speaker pre-extraction (Latin + Urdu name patterns)
• Graceful degradation — always returns a usable dict
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ── Constants ──────────────────────────────────────────────────────────────

MAX_WORDS_PER_CHUNK = 5_500
CHUNK_OVERLAP_WORDS = 300
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0

MODEL = "llama-3.3-70b-versatile"
FALLBACK_MODEL = "llama3-8b-8192"

# Required top-level keys and expected types
SCHEMA: dict[str, type | tuple] = {
    "meeting_title": str,
    "meeting_type": str,
    "summary": str,
    "sentiment": str,
    "duration_estimate": str,
    "attendees": list,
    "speaker_contributions": list,
    "action_items": list,
    "decisions": list,
    "open_questions": list,
    "risks": list,
    "next_steps": list,
    "key_topics": list,
    "stats": dict,
}

# Optional Urdu mirror keys (only present when output_language == "both")
URDU_MIRROR_KEYS = [
    "meeting_title_ur", "summary_ur", "next_steps_ur", "key_topics_ur",
]

ProgressCallback = Callable[[str], None]


# ── Client ─────────────────────────────────────────────────────────────────

def get_groq_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not found. Add it to your .env file.")
    return Groq(api_key=api_key)


# ── Speaker pre-extraction (Latin + Urdu) ─────────────────────────────────

def _extract_speakers(transcript: str) -> list[str]:
    """
    Pull speaker names from common patterns. Handles both Latin script names
    ('John Smith:') and Urdu script names ('علی:').
    """
    patterns = [
        r"^\s*([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s*:",        # "John Smith:"
        r"\[([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\]",            # "[John Smith]"
        r"<([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)>",              # "<John Smith>"
        r"^\s*([\u0600-\u06FF]+(?:\s[\u0600-\u06FF]+)?)\s*:",  # "علی خان:"
    ]
    found: set[str] = set()
    for pat in patterns:
        found.update(re.findall(pat, transcript, re.MULTILINE))

    # Filter common false positives (English + Urdu)
    noise = {
        "Note", "Action", "Decision", "Meeting", "Subject", "Date", "Re",
        "نوٹ", "میٹنگ", "تاریخ",
    }
    return sorted(found - noise)


# ── Language instruction blocks ───────────────────────────────────────────

_LANG_INSTRUCTIONS = {
    "english": """
LANGUAGE HANDLING:
- The transcript may be in English, Urdu, or mixed (Roman Urdu / code-switching).
- Understand ALL content regardless of source language.
- Write ALL output fields in ENGLISH ONLY.
- Translate any Urdu content into clear, professional English.
- Keep proper nouns (names, places, products) in their original form.
""",

    "urdu": """
LANGUAGE HANDLING:
- The transcript may be in Urdu, English, or mixed.
- Understand ALL content regardless of source language.
- Write ALL output fields in URDU (Nastaliq script — اردو).
- Use formal Urdu — avoid Roman Urdu.
- Keep proper nouns, technical terms, and English acronyms in their original form
  (e.g., "API", "Q3", "Microsoft" stay in English).
- Translate English content into Urdu naturally.
""",

    "both": """
LANGUAGE HANDLING:
- The transcript may be in English, Urdu, or mixed.
- Provide BILINGUAL output for narrative fields:
    * "meeting_title" → English; also add "meeting_title_ur" → Urdu
    * "summary"       → English; also add "summary_ur"       → Urdu (Nastaliq script)
    * "next_steps"    → English array; also add "next_steps_ur" → Urdu array
    * "key_topics"    → English array; also add "key_topics_ur" → Urdu array
- For all other fields (action_items, decisions, etc.), use English.
- Keep proper nouns and technical terms in their original form.
""",
}


# ── Prompt builder ─────────────────────────────────────────────────────────

def build_prompt(
    transcript: str,
    known_speakers: list[str] | None = None,
    is_chunk: bool = False,
    output_language: str = "english",
) -> str:
    chunk_note = (
        "\nNOTE: This is a PARTIAL transcript chunk. Extract only what is "
        "explicitly present; use [] or 'Not mentioned' for missing items.\n"
        if is_chunk else ""
    )

    speaker_hint = (
        f"\nDetected speakers (use for attribution): {', '.join(known_speakers)}\n"
        if known_speakers else ""
    )

    lang_block = _LANG_INSTRUCTIONS.get(output_language, _LANG_INSTRUCTIONS["english"])

    # Schema differs slightly when 'both' is requested
    extra_schema_lines = ""
    if output_language == "both":
        extra_schema_lines = """,
  "meeting_title_ur": "Urdu version of meeting_title",
  "summary_ur": "Urdu version of summary (Nastaliq script)",
  "next_steps_ur": ["Urdu translation of next_steps"],
  "key_topics_ur": ["Urdu translation of key_topics"]"""

    return f"""You are a senior meeting analyst with expertise in extracting \
actionable intelligence from meeting transcripts.{chunk_note}{speaker_hint}
{lang_block}

Analyze the transcript below and return ONLY a single valid JSON object.
No markdown fences, no commentary, no preamble — pure JSON.

Required schema (use exactly these keys):
{{
  "meeting_title": "Concise, descriptive title (max 10 words)",
  "meeting_type": "One of: standup | planning | retrospective | review | \
brainstorm | 1-on-1 | all-hands | client-call | interview | other",
  "summary": "3–4 paragraphs covering: (1) context and goals, \
(2) key discussions and debates, (3) outcomes and decisions, \
(4) blockers and next steps. Be specific — include names, numbers, dates.",
  "sentiment": "One of: positive | neutral | mixed | tense | unresolved",
  "duration_estimate": "e.g. '45 min' or 'Unknown'",
  "attendees": ["Full Name or handle as written in transcript"],
  "speaker_contributions": [
    {{
      "speaker": "Name",
      "role": "Role if mentioned, else 'Unknown'",
      "key_points": ["Main point 1", "Main point 2"],
      "items_owned": ["Action item or decision they own"]
    }}
  ],
  "action_items": [
    {{
      "task": "Clear, specific description starting with a verb",
      "owner": "Person responsible (or 'Unassigned')",
      "due_date": "Exact date/relative deadline if stated, else 'Not specified'",
      "priority": "High | Medium | Low",
      "context": "One sentence explaining why this matters"
    }}
  ],
  "decisions": [
    {{
      "decision": "What was decided (specific and unambiguous)",
      "rationale": "Why this was chosen (or 'Not mentioned')",
      "decided_by": "Who made or confirmed the decision",
      "impact": "Brief impact statement (or 'Not mentioned')"
    }}
  ],
  "open_questions": [
    {{
      "question": "The unresolved question or blocker",
      "assigned_to": "Who should resolve it (or 'Team')",
      "urgency": "High | Medium | Low"
    }}
  ],
  "risks": [
    {{
      "risk": "Description of the risk or concern raised",
      "likelihood": "High | Medium | Low | Unknown",
      "mitigation": "Suggested or agreed mitigation (or 'None discussed')"
    }}
  ],
  "next_steps": ["Ordered list of next actions (most urgent first)"],
  "key_topics": ["Topic tag 1", "Topic tag 2"],
  "stats": {{
    "action_item_count": 0,
    "decision_count": 0,
    "open_question_count": 0,
    "risk_count": 0,
    "attendee_count": 0
  }}{extra_schema_lines}
}}

Rules (non-negotiable):
1. NEVER invent or hallucinate information not in the transcript.
2. If something is missing, use [] or "Not mentioned" — never omit keys.
3. Action item tasks must start with an imperative verb.
4. Priority is High if a deadline or urgent language is mentioned, Low if vague.
5. stats values must be integers matching the actual array lengths.
6. Return ONLY the JSON object — no markdown, no explanation.

TRANSCRIPT:
{transcript}
"""


# ── JSON extraction & validation ───────────────────────────────────────────

def _extract_json(raw: str) -> str:
    """Strip code fences and extract the outermost {...} block."""
    text = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
    text = re.sub(r"```", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start: end + 1]


def _validate_and_repair(data: dict, client: Groq, raw: str) -> dict:
    """Ensure all required keys exist with correct types; attempt one repair if not."""
    missing = [k for k in SCHEMA if k not in data]
    wrong_type = [
        k for k, t in SCHEMA.items()
        if k in data and not isinstance(data[k], t)
    ]

    if not missing and not wrong_type:
        return data

    print(f"[summarizer] Schema issues — missing: {missing}, wrong type: {wrong_type}")

    repair_prompt = f"""The following JSON is malformed or incomplete.
Fix it so every key exists with the correct type. Return ONLY the corrected JSON.

Missing keys: {missing}
Wrong-type keys: {wrong_type}

JSON to fix:
{json.dumps(data, indent=2, ensure_ascii=False)[:3000]}
"""
    try:
        raw_repaired = _call_llm(client, repair_prompt)
        repaired = json.loads(_extract_json(raw_repaired))
        print("[summarizer] Repair successful.")
        return repaired
    except Exception as e:
        print(f"[summarizer] Repair failed: {e}. Using best-effort defaults.")
        defaults: dict[str, Any] = {
            "meeting_title": "Meeting Summary",
            "meeting_type": "other",
            "summary": raw[:500],
            "sentiment": "neutral",
            "duration_estimate": "Unknown",
            "attendees": [], "speaker_contributions": [], "action_items": [],
            "decisions": [], "open_questions": [], "risks": [],
            "next_steps": [], "key_topics": [],
            "stats": {
                "action_item_count": 0, "decision_count": 0,
                "open_question_count": 0, "risk_count": 0, "attendee_count": 0,
            },
        }
        for k in missing:
            data[k] = defaults[k]
        return data


def _parse_response(raw: str, client: Groq) -> dict[str, Any]:
    cleaned = _extract_json(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        print(f"[summarizer] JSON parse failed: {exc}")
        print(f"[summarizer] Raw snippet:\n{cleaned[:400]}")
        data = {
            "meeting_title": "Meeting Summary", "meeting_type": "other",
            "summary": raw, "sentiment": "neutral", "duration_estimate": "Unknown",
            "attendees": [], "speaker_contributions": [], "action_items": [],
            "decisions": [], "open_questions": [], "risks": [],
            "next_steps": [], "key_topics": [],
            "stats": {
                "action_item_count": 0, "decision_count": 0,
                "open_question_count": 0, "risk_count": 0, "attendee_count": 0,
            },
        }

    # Sync stats with actual array lengths
    data.setdefault("stats", {})
    data["stats"].update({
        "action_item_count": len(data.get("action_items", [])),
        "decision_count": len(data.get("decisions", [])),
        "open_question_count": len(data.get("open_questions", [])),
        "risk_count": len(data.get("risks", [])),
        "attendee_count": len(data.get("attendees", [])),
    })

    return _validate_and_repair(data, client, raw)


# ── LLM call with retries + model fallback ─────────────────────────────────

def _call_llm(client: Groq, prompt: str, model: str = MODEL) -> str:
    for current_model in (model, FALLBACK_MODEL):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.chat.completions.create(
                    model=current_model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a precise multilingual meeting analyst. "
                                "You handle English, Urdu, and code-switched content. "
                                "Always respond with a single valid JSON object only. "
                                "Never add markdown, code fences, or explanations."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.05,
                    max_tokens=4000,
                )
                return response.choices[0].message.content.strip()
            except Exception as exc:
                err = str(exc).lower()
                retryable = any(x in err for x in ("rate", "429", "500", "502", "503"))
                if retryable and attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    print(f"[summarizer] {current_model} — attempt {attempt} failed, "
                          f"retrying in {delay:.0f}s… ({exc})")
                    time.sleep(delay)
                elif retryable:
                    print(f"[summarizer] {current_model} exhausted, trying fallback…")
                    break
                else:
                    raise

    raise RuntimeError("LLM call failed after all retries and model fallback.")


# ── Chunking ───────────────────────────────────────────────────────────────

def _split_transcript(transcript: str) -> list[str]:
    words = transcript.split()
    if len(words) <= MAX_WORDS_PER_CHUNK:
        return [transcript]

    chunks, start = [], 0
    while start < len(words):
        end = min(start + MAX_WORDS_PER_CHUNK, len(words))
        chunks.append(" ".join(words[start:end]))
        start += MAX_WORDS_PER_CHUNK - CHUNK_OVERLAP_WORDS

    print(f"[summarizer] Transcript split into {len(chunks)} chunks "
          f"({len(words):,} words total).")
    return chunks


# ── Merge chunk results ────────────────────────────────────────────────────

def _deduplicate_dicts(items: list[dict], key: str) -> list[dict]:
    """Remove near-duplicate dicts using a key field (case-insensitive)."""
    seen: set[str] = set()
    result = []
    for item in items:
        fingerprint = item.get(key, "").lower().strip()[:80]
        if fingerprint not in seen:
            seen.add(fingerprint)
            result.append(item)
    return result


def _merge(parts: list[dict], output_language: str = "english") -> dict:
    if len(parts) == 1:
        return parts[0]

    def flat_unique(key: str) -> list:
        return list(dict.fromkeys(x for p in parts for x in p.get(key, [])))

    merged_actions = _deduplicate_dicts(
        [i for p in parts for i in p.get("action_items", [])], "task")
    merged_decisions = _deduplicate_dicts(
        [i for p in parts for i in p.get("decisions", [])], "decision")
    merged_questions = _deduplicate_dicts(
        [i for p in parts for i in p.get("open_questions", [])], "question")
    merged_risks = _deduplicate_dicts(
        [i for p in parts for i in p.get("risks", [])], "risk")
    merged_speakers = _deduplicate_dicts(
        [i for p in parts for i in p.get("speaker_contributions", [])], "speaker")

    merged = {
        "meeting_title": parts[0].get("meeting_title", "Meeting Summary"),
        "meeting_type": parts[0].get("meeting_type", "other"),
        "summary": "\n\n".join(p.get("summary", "") for p in parts if p.get("summary")),
        "sentiment": parts[0].get("sentiment", "neutral"),
        "duration_estimate": parts[0].get("duration_estimate", "Unknown"),
        "attendees": flat_unique("attendees"),
        "speaker_contributions": merged_speakers,
        "action_items": merged_actions,
        "decisions": merged_decisions,
        "open_questions": merged_questions,
        "risks": merged_risks,
        "next_steps": flat_unique("next_steps"),
        "key_topics": flat_unique("key_topics"),
        "stats": {
            "action_item_count": len(merged_actions),
            "decision_count": len(merged_decisions),
            "open_question_count": len(merged_questions),
            "risk_count": len(merged_risks),
            "attendee_count": len(flat_unique("attendees")),
        },
    }

    # Merge Urdu mirror fields if 'both' mode was used
    if output_language == "both":
        merged["meeting_title_ur"] = parts[0].get("meeting_title_ur", "")
        merged["summary_ur"] = "\n\n".join(
            p.get("summary_ur", "") for p in parts if p.get("summary_ur")
        )
        merged["next_steps_ur"] = flat_unique("next_steps_ur")
        merged["key_topics_ur"] = flat_unique("key_topics_ur")

    return merged


# ── Public API ─────────────────────────────────────────────────────────────

def summarize_transcript(
    transcript: str,
    progress: ProgressCallback | None = None,
    output_language: str = "english",
) -> dict[str, Any]:
    """
    Convert a raw meeting transcript into a rich structured summary.

    Args:
        transcript: Raw meeting transcript text (any language).
        progress:   Optional callback(message: str) for UI feedback.
        output_language: 'english' | 'urdu' | 'both'.

    Returns:
        Structured dict matching the schema in SCHEMA (plus Urdu mirror fields
        if output_language == 'both').
    """
    def _log(msg: str) -> None:
        print(f"[summarizer] {msg}")
        if progress:
            progress(msg)

    if output_language not in _LANG_INSTRUCTIONS:
        raise ValueError(
            f"output_language must be one of {list(_LANG_INSTRUCTIONS)}, "
            f"got {output_language!r}"
        )

    _log(f"Starting analysis (output language: {output_language})…")
    client = get_groq_client()

    known_speakers = _extract_speakers(transcript)
    if known_speakers:
        _log(f"Detected speakers: {', '.join(known_speakers)}")

    chunks = _split_transcript(transcript)
    results: list[dict] = []

    for i, chunk in enumerate(chunks, 1):
        _log(f"Processing chunk {i}/{len(chunks)}…")
        prompt = build_prompt(
            chunk,
            known_speakers=known_speakers,
            is_chunk=len(chunks) > 1,
            output_language=output_language,
        )
        raw = _call_llm(client, prompt)
        parsed = _parse_response(raw, client)
        results.append(parsed)
        _log(f"Chunk {i}/{len(chunks)} complete.")

    final = _merge(results, output_language=output_language)
    final["_output_language"] = output_language  # for downstream PDF generator

    _log(
        f"Done. {final['stats']['action_item_count']} actions | "
        f"{final['stats']['decision_count']} decisions | "
        f"{final['stats']['risk_count']} risks | "
        f"{final['stats']['attendee_count']} attendees"
    )
    return final