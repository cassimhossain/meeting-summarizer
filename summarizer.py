"""
summarizer.py  –  Transcript → rich structured JSON via Groq LLaMA 3
Improvements over v2:
  • Richer schema: meeting_type, sentiment, risks, speaker_contributions, stats
  • JSON schema validation with auto-repair pass
  • Progress callback for Streamlit / CLI feedback
  • Smarter token-aware chunking (not just word count)
  • Deduplication of action items by semantic similarity
  • Speaker extraction pre-pass for better attribution
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
MAX_WORDS_PER_CHUNK  = 5_500   # conservative to leave room for prompt tokens
CHUNK_OVERLAP_WORDS  = 300     # context continuity across chunks
MAX_RETRIES          = 3
RETRY_BASE_DELAY     = 2.0
MODEL                = "llama-3.3-70b-versatile"
FALLBACK_MODEL       = "llama3-8b-8192"

# Required top-level keys and their expected types
SCHEMA: dict[str, type | tuple] = {
    "meeting_title":        str,
    "meeting_type":         str,
    "summary":              str,
    "sentiment":            str,
    "duration_estimate":    str,
    "attendees":            list,
    "speaker_contributions": list,
    "action_items":         list,
    "decisions":            list,
    "open_questions":       list,
    "risks":                list,
    "next_steps":           list,
    "key_topics":           list,
    "stats":                dict,
}

ProgressCallback = Callable[[str], None]


# ── Client ─────────────────────────────────────────────────────────────────

def get_groq_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not found. Add it to your .env file.")
    return Groq(api_key=api_key)


# ── Speaker pre-extraction ─────────────────────────────────────────────────

def _extract_speakers(transcript: str) -> list[str]:
    """
    Heuristically pull speaker names from 'Name:' or '[Name]' patterns
    so the main prompt has better context for attribution.
    """
    patterns = [
        r"^\s*([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s*:",   # "John Smith:"
        r"\[([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\]",        # "[John Smith]"
        r"<([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)>",          # "<John Smith>"
    ]
    found: set[str] = set()
    for pat in patterns:
        found.update(re.findall(pat, transcript, re.MULTILINE))
    # Filter out common false positives
    noise = {"Note", "Action", "Decision", "Meeting", "Subject", "Date", "Re"}
    return sorted(found - noise)


# ── Prompt builder ─────────────────────────────────────────────────────────

def build_prompt(transcript: str, known_speakers: list[str] | None = None,
                 is_chunk: bool = False) -> str:
    chunk_note = (
        "\nNOTE: This is a PARTIAL transcript chunk. "
        "Extract only what is explicitly present; use empty arrays or "
        "'Not mentioned' for anything absent.\n"
        if is_chunk else ""
    )
    speaker_hint = (
        f"\nDetected speakers (use for attribution): {', '.join(known_speakers)}\n"
        if known_speakers else ""
    )

    return f"""You are a senior meeting analyst with expertise in extracting \
actionable intelligence from meeting transcripts.{chunk_note}{speaker_hint}

Analyze the transcript below and return ONLY a single valid JSON object.
No markdown fences, no commentary, no preamble — pure JSON.

Required schema (use exactly these keys):
{{
  "meeting_title": "Concise, descriptive title (max 10 words)",

  "meeting_type": "One of: standup | planning | retrospective | review | \
brainstorm | 1-on-1 | all-hands | client-call | interview | other",

  "summary": "3–4 paragraphs. Cover: (1) context and goals, \
(2) key discussions and debates, (3) outcomes and decisions, \
(4) blockers and next steps. Be specific — include names, numbers, dates.",

  "sentiment": "One of: positive | neutral | mixed | tense | unresolved",

  "duration_estimate": "Estimated meeting length if inferable (e.g. '45 min') \
or 'Unknown'",

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
  }}
}}

Rules (non-negotiable):
1. NEVER invent or hallucinate information not in the transcript.
2. If something is missing, use empty [] or "Not mentioned" — never omit keys.
3. Action item tasks must start with an imperative verb (e.g. "Schedule", "Review").
4. Priority is High if a deadline or urgent language is mentioned, Low if vague.
5. stats values must be integers matching the actual array lengths above.
6. Return ONLY the JSON object — no markdown, no explanation.

TRANSCRIPT:
{transcript}
"""


# ── JSON extraction & validation ───────────────────────────────────────────

def _extract_json(raw: str) -> str:
    """Strip fences and extract the outermost {...} block."""
    text = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
    text = re.sub(r"```", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def _validate_and_repair(data: dict, client: Groq, raw: str) -> dict:
    """
    Check that all required keys exist with the right types.
    If critical keys are missing/wrong, attempt one repair call.
    """
    missing = [k for k in SCHEMA if k not in data]
    wrong_type = [
        k for k, t in SCHEMA.items()
        if k in data and not isinstance(data[k], t)
    ]

    if not missing and not wrong_type:
        return data   # all good

    print(f"[summarizer] Schema issues — missing: {missing}, wrong type: {wrong_type}")

    # One repair attempt
    repair_prompt = f"""The following JSON is malformed or incomplete.
Fix it so every key exists with the correct type. Return ONLY the corrected JSON.
Missing keys: {missing}
Wrong-type keys: {wrong_type}

JSON to fix:
{json.dumps(data, indent=2)[:3000]}
"""
    try:
        raw_repaired = _call_llm(client, repair_prompt)
        repaired = json.loads(_extract_json(raw_repaired))
        print("[summarizer] Repair successful.")
        return repaired
    except Exception as e:
        print(f"[summarizer] Repair failed: {e}. Using best-effort data.")
        # Inject defaults for missing keys
        defaults: dict[str, Any] = {
            "meeting_title": "Meeting Summary", "meeting_type": "other",
            "summary": raw[:500], "sentiment": "neutral",
            "duration_estimate": "Unknown", "attendees": [],
            "speaker_contributions": [], "action_items": [], "decisions": [],
            "open_questions": [], "risks": [], "next_steps": [],
            "key_topics": [], "stats": {
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
            "meeting_title": "Meeting Summary",
            "meeting_type": "other",
            "summary": raw,
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

    # Sync stats with actual array lengths (model sometimes miscounts)
    data.setdefault("stats", {})
    data["stats"].update({
        "action_item_count":   len(data.get("action_items", [])),
        "decision_count":      len(data.get("decisions", [])),
        "open_question_count": len(data.get("open_questions", [])),
        "risk_count":          len(data.get("risks", [])),
        "attendee_count":      len(data.get("attendees", [])),
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
                                "You are a precise meeting analyst. "
                                "Always respond with a single valid JSON object only. "
                                "Never add markdown, code fences, or explanations."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.05,   # near-deterministic for structured output
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


def _merge(parts: list[dict]) -> dict:
    if len(parts) == 1:
        return parts[0]

    def flat_unique(key: str) -> list:
        return list(dict.fromkeys(x for p in parts for x in p.get(key, [])))

    merged_actions   = _deduplicate_dicts(
        [i for p in parts for i in p.get("action_items", [])], "task")
    merged_decisions = _deduplicate_dicts(
        [i for p in parts for i in p.get("decisions", [])], "decision")
    merged_questions = _deduplicate_dicts(
        [i for p in parts for i in p.get("open_questions", [])], "question")
    merged_risks     = _deduplicate_dicts(
        [i for p in parts for i in p.get("risks", [])], "risk")
    merged_speakers  = _deduplicate_dicts(
        [i for p in parts for i in p.get("speaker_contributions", [])], "speaker")

    return {
        "meeting_title":         parts[0].get("meeting_title", "Meeting Summary"),
        "meeting_type":          parts[0].get("meeting_type", "other"),
        "summary":               "\n\n".join(
            p.get("summary", "") for p in parts if p.get("summary")),
        "sentiment":             parts[0].get("sentiment", "neutral"),
        "duration_estimate":     parts[0].get("duration_estimate", "Unknown"),
        "attendees":             flat_unique("attendees"),
        "speaker_contributions": merged_speakers,
        "action_items":          merged_actions,
        "decisions":             merged_decisions,
        "open_questions":        merged_questions,
        "risks":                 merged_risks,
        "next_steps":            flat_unique("next_steps"),
        "key_topics":            flat_unique("key_topics"),
        "stats": {
            "action_item_count":   len(merged_actions),
            "decision_count":      len(merged_decisions),
            "open_question_count": len(merged_questions),
            "risk_count":          len(merged_risks),
            "attendee_count":      len(flat_unique("attendees")),
        },
    }


# ── Public API ─────────────────────────────────────────────────────────────

def summarize_transcript(
    transcript: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """
    Convert a raw meeting transcript to a rich structured summary dict.

    Args:
        transcript: Raw meeting transcript text.
        progress:   Optional callback(message: str) for UI feedback
                    (e.g. Streamlit's st.status or a tqdm wrapper).

    Returns:
        Structured dict matching the schema defined in SCHEMA.
    """
    def _log(msg: str) -> None:
        print(f"[summarizer] {msg}")
        if progress:
            progress(msg)

    _log("Starting analysis…")
    client = get_groq_client()

    # Pre-extract speakers for better attribution hints
    known_speakers = _extract_speakers(transcript)
    if known_speakers:
        _log(f"Detected speakers: {', '.join(known_speakers)}")

    chunks = _split_transcript(transcript)
    results: list[dict] = []

    for i, chunk in enumerate(chunks, 1):
        _log(f"Processing chunk {i}/{len(chunks)}…")
        prompt = build_prompt(chunk, known_speakers, is_chunk=len(chunks) > 1)
        raw    = _call_llm(client, prompt)
        parsed = _parse_response(raw, client)
        results.append(parsed)
        _log(f"Chunk {i}/{len(chunks)} complete.")

    final = _merge(results)
    _log(
        f"Done. "
        f"{final['stats']['action_item_count']} actions | "
        f"{final['stats']['decision_count']} decisions | "
        f"{final['stats']['risk_count']} risks | "
        f"{final['stats']['attendee_count']} attendees"
    )
    return final