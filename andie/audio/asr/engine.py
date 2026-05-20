"""
andie/audio/asr/engine.py
Automatic Speech Recognition via faster-whisper.

Usage:
    from andie_backend.andie.audio.asr.engine import transcribe
    result = transcribe(audio_b64, language="en")

Returns:
    {
        "text": str,
        "segments": [...],
        "language": str,
        "language_probability": float,
        "elapsed_ms": int,
        "events": [...],   # detected semantic events from transcript
        "engine": "faster-whisper",
    }

Model is loaded lazily on first call and cached for the process lifetime.
Default model: "base" (good CPU performance, ~150MB).
Override via env ANDIE_WHISPER_MODEL=small|medium|large-v3.
"""
from __future__ import annotations

import base64
import io
import os
import time
from typing import Optional

# ── Lazy model cache ─────────────────────────────────────────────────────────
_model = None
_MODEL_SIZE = os.environ.get("ANDIE_WHISPER_MODEL", "base")
_DEVICE     = os.environ.get("ANDIE_WHISPER_DEVICE", "cpu")
_COMPUTE    = os.environ.get("ANDIE_WHISPER_COMPUTE", "int8")


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(_MODEL_SIZE, device=_DEVICE, compute_type=_COMPUTE)
    return _model


# ── Semantic event detection from transcript ─────────────────────────────────
_EVENT_PATTERNS: list[tuple[str, str, str]] = [
    # (keyword, event_type, severity)
    ("error",       "asr:error",       "high"),
    ("exception",   "asr:error",       "high"),
    ("failed",      "asr:failure",     "high"),
    ("crash",       "asr:failure",     "critical"),
    ("warning",     "asr:warning",     "medium"),
    ("alert",       "asr:alert",       "medium"),
    ("success",     "asr:success",     "low"),
    ("done",        "asr:done",        "info"),
    ("complete",    "asr:done",        "info"),
    ("andie",       "asr:wakeword",    "info"),
    ("hey andie",   "asr:wakeword",    "info"),
    ("stop",        "asr:command",     "info"),
    ("cancel",      "asr:command",     "info"),
    ("run",         "asr:command",     "info"),
    ("deploy",      "asr:command",     "info"),
    ("build",       "asr:command",     "info"),
    ("docker",      "asr:system",      "info"),
    ("git",         "asr:system",      "info"),
    ("gpu",         "asr:system",      "info"),
]


def _detect_events(text: str) -> list[dict]:
    lower = text.lower()
    seen: set[str] = set()
    events: list[dict] = []
    for keyword, ev_type, severity in _EVENT_PATTERNS:
        if keyword in lower and ev_type not in seen:
            seen.add(ev_type)
            events.append({
                "type": ev_type,
                "value": keyword,
                "severity": severity,
                "meta": {"source_text": text[:200]},
            })
    return events


# ── Public API ───────────────────────────────────────────────────────────────
def transcribe(
    audio_b64: str,
    language: Optional[str] = None,
    task: str = "transcribe",
) -> dict:
    """
    Transcribe audio from base64-encoded bytes (WebM, WAV, MP3, OGG).
    Strips data-URL prefix if present.
    """
    t0 = time.perf_counter()

    # Decode base64
    if "," in audio_b64:
        audio_b64 = audio_b64.split(",", 1)[1]
    audio_bytes = base64.b64decode(audio_b64)
    audio_file = io.BytesIO(audio_bytes)

    model = _get_model()
    kwargs: dict = {"task": task, "beam_size": 5}
    if language:
        kwargs["language"] = language

    segments_gen, info = model.transcribe(audio_file, **kwargs)
    segments = []
    full_text_parts = []
    for seg in segments_gen:
        segments.append({
            "start": round(seg.start, 3),
            "end":   round(seg.end, 3),
            "text":  seg.text.strip(),
            "avg_logprob": round(seg.avg_logprob, 4),
        })
        full_text_parts.append(seg.text.strip())

    full_text = " ".join(full_text_parts)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    events = _detect_events(full_text)

    return {
        "text":                 full_text,
        "segments":             segments,
        "language":             info.language,
        "language_probability": round(info.language_probability, 4),
        "elapsed_ms":           elapsed_ms,
        "events":               events,
        "engine":               f"faster-whisper/{_MODEL_SIZE}",
    }
