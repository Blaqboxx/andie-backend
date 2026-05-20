"""
andie/audio/prompts/store.py
Named audio prompt registry.

Prompts are registered with a name, text, and optional voice/rate/pitch.
Audio is synthesized on first access and cached in-process.

Built-in system prompts are registered at import time.

Usage:
    from andie_backend.andie.audio.prompts.store import register, get, list_prompts
    await register("startup", "ANDIE online. All systems nominal.")
    result = await get("startup")   # {"audio_b64": ..., "voice": ..., ...}
"""
from __future__ import annotations

import asyncio
from typing import Optional

# ── Prompt definition ─────────────────────────────────────────────────────────
class AudioPrompt:
    __slots__ = ("name", "text", "voice", "rate", "pitch", "_cache")

    def __init__(
        self,
        name: str,
        text: str,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
        pitch: Optional[str] = None,
    ):
        self.name  = name
        self.text  = text
        self.voice = voice
        self.rate  = rate
        self.pitch = pitch
        self._cache: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "name":   self.name,
            "text":   self.text,
            "voice":  self.voice,
            "rate":   self.rate,
            "pitch":  self.pitch,
            "cached": self._cache is not None,
        }


# ── Registry ──────────────────────────────────────────────────────────────────
_registry: dict[str, AudioPrompt] = {}


def register(
    name: str,
    text: str,
    voice: Optional[str] = None,
    rate: Optional[str] = None,
    pitch: Optional[str] = None,
) -> AudioPrompt:
    """Register or overwrite a named prompt. Clears cached audio."""
    prompt = AudioPrompt(name=name, text=text, voice=voice, rate=rate, pitch=pitch)
    _registry[name] = prompt
    return prompt


def unregister(name: str) -> bool:
    if name in _registry:
        del _registry[name]
        return True
    return False


def list_prompts() -> list[dict]:
    return [p.to_dict() for p in _registry.values()]


async def get(name: str, force_regen: bool = False) -> Optional[dict]:
    """
    Get audio for a named prompt, synthesizing and caching on first access.
    Returns the TTS result dict or None if not found.
    """
    prompt = _registry.get(name)
    if prompt is None:
        return None

    if prompt._cache is None or force_regen:
        from andie_backend.andie.audio.tts.engine import synthesize
        result = await synthesize(
            prompt.text,
            voice=prompt.voice,
            rate=prompt.rate,
            pitch=prompt.pitch,
        )
        prompt._cache = result

    return {**prompt._cache, "prompt_name": name, "prompt_text": prompt.text}


async def speak(name: str) -> Optional[dict]:
    """Alias for get() — returns cached or synthesized audio for named prompt."""
    return await get(name)


# ── Built-in system prompts ───────────────────────────────────────────────────
_SYSTEM_PROMPTS = [
    ("startup",        "ANDIE online. All systems nominal.",                None,    "+0%",   "+0Hz"),
    ("shutdown",       "Shutting down. Goodbye.",                           None,    "-5%",   "-5Hz"),
    ("ready",          "Ready.",                                             None,    "+0%",   "+0Hz"),
    ("error",          "Error detected. Please check the system log.",      None,    "+0%",   "-10Hz"),
    ("task_complete",  "Task complete.",                                     None,    "+5%",   "+5Hz"),
    ("thinking",       "Processing. Please wait.",                          None,    "-10%",  "+0Hz"),
    ("alert",          "Alert. Immediate attention required.",               None,    "+10%",  "+10Hz"),
    ("observation",    "Observation loop running. Monitoring all domains.", None,    "+0%",   "+0Hz"),
    ("media_active",   "Media sensor active.",                              None,    "+0%",   "+0Hz"),
    ("wake",           "Yes?",                                              None,    "+0%",   "+0Hz"),
]

for _name, _text, _voice, _rate, _pitch in _SYSTEM_PROMPTS:
    register(_name, _text, voice=_voice, rate=_rate, pitch=_pitch)
