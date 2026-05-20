"""
andie/audio/wakewords/detector.py
Wake word and command keyword detection.

Two modes:
  1. Transcript-based (always available) — regex match on ASR output.
  2. Hardware-based (optional) — openwakeword on raw PCM audio frames.

The transcript-based detector is the primary path.
Hardware mode is activated if openwakeword is installed and
ANDIE_WAKEWORD_HW=1 is set in the environment.

Usage:
    from andie_backend.andie.audio.wakewords.detector import check_transcript, WAKEWORDS
    hits = check_transcript("hey andie, start the deployment")
    # [{"word": "hey andie", "type": "wake", "confidence": 1.0}]
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

# ── Wake word registry ────────────────────────────────────────────────────────
# (pattern, canonical_word, type, base_confidence)
_REGISTRY: list[tuple[re.Pattern, str, str, float]] = []


def _add(pattern: str, canonical: str, ww_type: str, confidence: float = 1.0) -> None:
    _REGISTRY.append((re.compile(pattern, re.IGNORECASE), canonical, ww_type, confidence))


# Core wake words
_add(r"\bhey\s+andie\b",          "hey andie",    "wake",    1.0)
_add(r"\bandie\b",                "andie",        "wake",    0.8)
_add(r"\bwake\s+up\b",            "wake up",      "wake",    0.9)

# Command keywords
_add(r"\bstop\b",                 "stop",         "command", 1.0)
_add(r"\bcancel\b",               "cancel",       "command", 1.0)
_add(r"\bpause\b",                "pause",        "command", 1.0)
_add(r"\bresume\b",               "resume",       "command", 1.0)
_add(r"\brun\s+deploy(?:ment)?\b","run deploy",   "command", 1.0)
_add(r"\bdeploy\b",               "deploy",       "command", 0.9)
_add(r"\bbuild\b",                "build",        "command", 0.9)
_add(r"\brestart\b",              "restart",      "command", 1.0)
_add(r"\bshutdown\b",             "shutdown",     "command", 1.0)
_add(r"\bstatus\b",               "status",       "command", 0.9)
_add(r"\bdiagnose\b",             "diagnose",     "command", 1.0)
_add(r"\bobserve\b",              "observe",      "command", 0.9)

# System keywords (informational)
_add(r"\berror\b",                "error",        "system",  1.0)
_add(r"\bcritical\b",             "critical",     "system",  1.0)
_add(r"\bfailed?\b",              "failed",       "system",  1.0)
_add(r"\bwarning\b",              "warning",      "system",  0.9)
_add(r"\bgpu\b",                  "gpu",          "system",  0.8)
_add(r"\bdocker\b",               "docker",       "system",  0.8)
_add(r"\bgit\b",                  "git",          "system",  0.7)


def check_transcript(text: str) -> list[dict]:
    """
    Scan transcript text for registered wake words and keywords.
    Returns list of detected matches (deduped by canonical word).
    """
    seen: set[str] = set()
    hits: list[dict] = []
    ts = time.time()

    for pattern, canonical, ww_type, confidence in _REGISTRY:
        if canonical in seen:
            continue
        m = pattern.search(text)
        if m:
            seen.add(canonical)
            hits.append({
                "word":       canonical,
                "type":       ww_type,
                "confidence": confidence,
                "match":      m.group(0),
                "ts":         ts,
            })

    # Sort: wake first, then command, then system
    _order = {"wake": 0, "command": 1, "system": 2}
    hits.sort(key=lambda h: (_order.get(h["type"], 9), -h["confidence"]))
    return hits


def register_word(
    pattern: str,
    canonical: str,
    ww_type: str = "custom",
    confidence: float = 1.0,
) -> None:
    """Register a new wake word pattern at runtime."""
    _add(pattern, canonical, ww_type, confidence)


def list_words() -> list[dict]:
    """List all registered wake word patterns."""
    return [
        {"pattern": p.pattern, "canonical": c, "type": t, "confidence": cf}
        for p, c, t, cf in _REGISTRY
    ]


# ── Hardware mode (openwakeword, optional) ───────────────────────────────────
_hw_model = None
_HW_ENABLED = os.environ.get("ANDIE_WAKEWORD_HW", "0") == "1"


def check_audio_frame(pcm_frame: bytes) -> list[dict]:
    """
    Check a raw PCM audio frame for wake words using openwakeword.
    Requires openwakeword installed and ANDIE_WAKEWORD_HW=1.
    Returns [] if hardware mode is not available.
    """
    if not _HW_ENABLED:
        return []

    global _hw_model
    try:
        if _hw_model is None:
            import openwakeword
            from openwakeword.model import Model
            _hw_model = Model(inference_framework="onnx")

        import numpy as np
        audio_np = np.frombuffer(pcm_frame, dtype=np.int16)
        scores = _hw_model.predict(audio_np)
        hits = []
        for model_name, score in scores.items():
            if score > 0.5:
                hits.append({
                    "word":       model_name,
                    "type":       "wake",
                    "confidence": float(score),
                    "source":     "openwakeword",
                    "ts":         time.time(),
                })
        return hits
    except Exception:
        return []
