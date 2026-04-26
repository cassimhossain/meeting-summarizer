"""
transcriber.py — Bilingual (English + Urdu) audio transcription using Whisper

Key improvements over v1:
• Language-aware transcription (English, Urdu, or auto-detect)
• Code-switching support (Urdu + English mixed in one meeting — common in Pakistan)
• Smarter model selection — 'small' minimum for Urdu accuracy
• Translation mode — transcribe Urdu audio directly to English text
• Progress callbacks for Streamlit UI
• Word-level timestamps for downstream speaker diarization hints
• Cleaner FFmpeg error messages
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable

import whisper

ProgressCallback = Callable[[str], None]


# ── Constants ──────────────────────────────────────────────────────────────

# Recommended models per language
# - English-only meetings:     'base' is fine (~140 MB, fast)
# - Urdu / mixed meetings:     'small' minimum (~460 MB), 'medium' for best quality
# - Critical accuracy needed:  'medium' (~1.5 GB) or 'large' (~3 GB)
DEFAULT_MODEL = "small"

# Map UI labels → Whisper language codes
LANGUAGE_CODES: dict[str, str | None] = {
    "auto": None,        # auto-detect (recommended for mixed meetings)
    "english": "en",
    "urdu": "ur",
}


# ── FFmpeg conversion ──────────────────────────────────────────────────────

def convert_to_wav(input_path: str) -> str:
    """
    Convert any audio/video file to 16 kHz mono WAV — Whisper's preferred format.

    Returns the path to the converted WAV file.
    Raises RuntimeError with a clean message on failure.
    """
    output_path = input_path.rsplit(".", 1)[0] + "_converted.wav"

    command = [
        "ffmpeg",
        "-i", input_path,
        "-ar", "16000",      # 16 kHz sample rate
        "-ac", "1",          # mono channel
        "-loglevel", "error",
        "-y",                # overwrite if exists
        output_path,
    ]

    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        # Surface only the relevant FFmpeg error, not the full noisy output
        error = result.stderr.strip().split("\n")[-1] if result.stderr else "unknown"
        raise RuntimeError(f"FFmpeg conversion failed: {error}")

    return output_path


# ── Main transcription ────────────────────────────────────────────────────

def transcribe_audio(
    file_path: str,
    model_size: str = DEFAULT_MODEL,
    language: str | None = None,
    translate_to_english: bool = False,
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Transcribe an audio file using local Whisper.

    Args:
        file_path: Path to input audio/video file.
        model_size: 'tiny' | 'base' | 'small' | 'medium' | 'large'.
                    Use 'small' or higher for Urdu — 'base' is unreliable for it.
        language:   'en' for English, 'ur' for Urdu, None to auto-detect.
                    Auto-detect handles code-switched meetings best.
        translate_to_english: If True, Whisper translates non-English speech
                              to English text directly. Useful when you want
                              an English summary from Urdu audio.
        progress:   Optional callback for UI updates.

    Returns:
        {
            'text': str,           # full transcript
            'language': str,       # detected/used language code
            'segments': list,      # timestamped segments (for downstream use)
            'duration': float,     # audio duration in seconds
        }
    """
    def _log(msg: str) -> None:
        print(f"[transcriber] {msg}")
        if progress:
            progress(msg)

    # Step 1: Convert to Whisper-friendly WAV
    _log("Converting audio to 16 kHz mono WAV…")
    wav_path = convert_to_wav(file_path)

    try:
        # Step 2: Load Whisper model (cached after first download)
        _log(f"Loading Whisper '{model_size}' model… (first run downloads ~MB)")
        model = whisper.load_model(model_size)

        # Step 3: Build transcription options
        options: dict = {
            "fp16": False,        # CPU-safe; flip to True if running on GPU
            "verbose": False,
            "word_timestamps": True,
        }

        if language:
            options["language"] = language

        # Translation mode: Whisper outputs English even from Urdu audio
        if translate_to_english:
            options["task"] = "translate"
            _log("Translation mode ON — output will be in English.")
        else:
            options["task"] = "transcribe"

        # Step 4: Run transcription
        _log("Transcribing… (this can take 1–5 min depending on audio length)")
        result = model.transcribe(wav_path, **options)

        detected_lang = result.get("language", language or "unknown")
        _log(f"Done. Detected language: {detected_lang}")

        # Compute duration from segments if available
        segments = result.get("segments", [])
        duration = segments[-1]["end"] if segments else 0.0

        return {
            "text": result["text"].strip(),
            "language": detected_lang,
            "segments": segments,
            "duration": duration,
        }

    finally:
        # Always clean up temp WAV, even if transcription failed
        if os.path.exists(wav_path):
            os.remove(wav_path)


# ── Streamlit upload helper ───────────────────────────────────────────────

def save_uploaded_file(uploaded_file, save_dir: str = "temp_audio") -> str:
    """Save a Streamlit UploadedFile to disk and return its path."""
    os.makedirs(save_dir, exist_ok=True)
    file_path = os.path.join(save_dir, uploaded_file.name)
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return file_path


# ── Lightweight speaker hint extraction ───────────────────────────────────

def extract_speaker_segments(segments: list, min_gap: float = 1.5) -> list[dict]:
    """
    Lightweight 'pseudo-diarization' — group consecutive segments separated
    by silence > min_gap seconds into speaker turns. Not real diarization,
    but a useful hint for the summarizer when no speaker labels are present.

    Returns list of {'start', 'end', 'text', 'turn_id'}.
    """
    if not segments:
        return []

    turns = []
    current_turn = {
        "turn_id": 1,
        "start": segments[0]["start"],
        "end": segments[0]["end"],
        "text": segments[0]["text"].strip(),
    }

    for seg in segments[1:]:
        gap = seg["start"] - current_turn["end"]
        if gap > min_gap:
            turns.append(current_turn)
            current_turn = {
                "turn_id": len(turns) + 1,
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"].strip(),
            }
        else:
            current_turn["end"] = seg["end"]
            current_turn["text"] += " " + seg["text"].strip()

    turns.append(current_turn)
    return turns