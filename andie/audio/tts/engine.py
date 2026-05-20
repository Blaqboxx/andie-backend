"""
andie/audio/tts/engine.py
Text-to-speech synthesis via edge-tts (Microsoft Edge TTS, free).

Usage:
    from andie_backend.andie.audio.tts.engine import synthesize
    result = await synthesize("Hello, I am ANDIE.")

Returns:
    {
        "audio_b64": str,       # base64-encoded MP3
        "content_type": "audio/mpeg",
        "voice": str,
        "elapsed_ms": int,
        "char_count": int,
    }

Default voice: ANDIE_TTS_VOICE env var or "en-US-AriaNeural".
Available voices: run `edge-tts --list-voices` or GET /audio/tts/voices.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import time
from typing import Optional

import edge_tts

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_VOICE = os.environ.get("ANDIE_TTS_VOICE", "en-US-AriaNeural")
DEFAULT_RATE  = os.environ.get("ANDIE_TTS_RATE",  "+0%")
DEFAULT_PITCH = os.environ.get("ANDIE_TTS_PITCH", "+0Hz")

# ── Voice list cache ──────────────────────────────────────────────────────────
_voices_cache: Optional[list[dict]] = None


async def list_voices(locale_filter: Optional[str] = None) -> list[dict]:
    global _voices_cache
    if _voices_cache is None:
        raw = await edge_tts.list_voices()
        _voices_cache = [
            {
                "name":        v["ShortName"],
                "locale":      v["Locale"],
                "gender":      v["Gender"],
                "description": v.get("FriendlyName", ""),
            }
            for v in raw
        ]
    if locale_filter:
        return [v for v in _voices_cache if v["locale"].startswith(locale_filter)]
    return _voices_cache


# ── Synthesis ─────────────────────────────────────────────────────────────────
async def synthesize(
    text: str,
    voice: Optional[str] = None,
    rate: Optional[str] = None,
    pitch: Optional[str] = None,
) -> dict:
    """
    Synthesize text to MP3 audio using edge-tts.
    Returns base64-encoded audio and metadata.
    """
    t0 = time.perf_counter()
    voice = voice or DEFAULT_VOICE
    rate  = rate  or DEFAULT_RATE
    pitch = pitch or DEFAULT_PITCH

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])

    audio_bytes = buf.getvalue()
    audio_b64 = base64.b64encode(audio_bytes).decode()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    return {
        "audio_b64":    audio_b64,
        "content_type": "audio/mpeg",
        "voice":        voice,
        "rate":         rate,
        "pitch":        pitch,
        "elapsed_ms":   elapsed_ms,
        "char_count":   len(text),
        "byte_size":    len(audio_bytes),
    }
