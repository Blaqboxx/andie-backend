"""
andie/audio/router.py
FastAPI router for the Audio Runtime Layer.

Endpoints:
  POST  /audio/asr/transcribe          — transcribe base64 audio
  POST  /audio/tts/synthesize          — synthesize text to audio
  POST  /audio/tts/ssml                — synthesize SSML to audio
  GET   /audio/tts/voices              — list available TTS voices
  GET   /audio/events                  — audio event ring buffer
  GET   /audio/events/summary          — event bus summary
  POST  /audio/wakewords/check         — check transcript for wake words
  GET   /audio/wakewords               — list registered wake words
  POST  /audio/wakewords/register      — register new wake word
  GET   /audio/prompts                 — list named prompts
  POST  /audio/prompts/register        — register named prompt
  GET   /audio/prompts/{name}          — synthesize/get named prompt
  DELETE /audio/prompts/{name}         — remove named prompt
  WS    /audio/stream                  — streaming ASR pipeline
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, Query
from pydantic import BaseModel

from andie_backend.andie.audio import events as _ev_pkg
from andie_backend.andie.audio.events import bus as _bus
from andie_backend.andie.audio.asr import engine as _asr
from andie_backend.andie.audio.tts import engine as _tts
from andie_backend.andie.audio.ssml import parser as _ssml
from andie_backend.andie.audio.prompts import store as _prompts
from andie_backend.andie.audio.wakewords import detector as _wakewords
from andie_backend.andie.audio.streaming import stream as _stream

router = APIRouter(prefix="/audio", tags=["audio"])

# Subscribe stream events to broadcast to all WS clients
def _on_audio_event(ev: dict) -> None:
    asyncio.get_event_loop().create_task(
        _stream.manager.broadcast({"type": "event", "event": ev})
    )

_bus.subscribe(_on_audio_event)


# ── ASR ───────────────────────────────────────────────────────────────────────
class TranscribeRequest(BaseModel):
    audio: str               # base64 or data URL
    language: Optional[str] = None
    task: str = "transcribe" # "transcribe" | "translate"

@router.post("/asr/transcribe")
async def asr_transcribe(req: TranscribeRequest):
    """
    Transcribe base64-encoded audio (WebM, WAV, MP3, OGG) using faster-whisper.
    Detects semantic events and wake words in the transcript.
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _asr.transcribe, req.audio, req.language, req.task)

    if result.get("events"):
        _bus.push_events(result["events"], source="asr")

    text = result.get("text", "")
    wakewords = _wakewords.check_transcript(text) if text else []
    if wakewords:
        for hit in wakewords:
            _bus.push_event({
                "type":     f"wakeword:{hit['word']}",
                "value":    hit["word"],
                "severity": "medium" if hit["type"] == "wake" else "info",
                "meta":     hit,
            }, source="wakeword-detector")

    return {**result, "wakewords": wakewords}


# ── TTS ───────────────────────────────────────────────────────────────────────
class SynthesizeRequest(BaseModel):
    text: str
    voice: Optional[str] = None
    rate:  Optional[str] = None
    pitch: Optional[str] = None

@router.post("/tts/synthesize")
async def tts_synthesize(req: SynthesizeRequest):
    """Synthesize plain text to MP3 audio using edge-tts."""
    return await _tts.synthesize(req.text, voice=req.voice, rate=req.rate, pitch=req.pitch)


class SSMLRequest(BaseModel):
    ssml: str
    voice: Optional[str] = None

@router.post("/tts/ssml")
async def tts_ssml(req: SSMLRequest):
    """Parse SSML and synthesize to audio. Voice overrides SSML voice attribute."""
    directives = _ssml.parse(req.ssml)
    params = _ssml.directives_to_tts_params(directives)
    voice = req.voice or params.get("voice")
    return await _tts.synthesize(
        params["text"],
        voice=voice,
        rate=params.get("rate"),
        pitch=params.get("pitch"),
    )


@router.get("/tts/voices")
async def tts_voices(locale: Optional[str] = Query(None)):
    """List available edge-tts voices, optionally filtered by locale prefix."""
    voices = await _tts.list_voices(locale_filter=locale)
    return {"voices": voices, "count": len(voices)}


# ── Events ────────────────────────────────────────────────────────────────────
@router.get("/events")
async def audio_events(
    limit: int = 50,
    severity: Optional[str] = None,
    since: Optional[float] = None,
):
    return {"events": _bus.get_events(limit=limit, severity=severity, since=since)}


@router.get("/events/summary")
async def audio_events_summary():
    return _bus.summary()


# ── Wake words ────────────────────────────────────────────────────────────────
class CheckTranscriptRequest(BaseModel):
    text: str

@router.post("/wakewords/check")
async def wakewords_check(req: CheckTranscriptRequest):
    hits = _wakewords.check_transcript(req.text)
    return {"hits": hits, "count": len(hits)}


@router.get("/wakewords")
async def wakewords_list():
    return {"words": _wakewords.list_words()}


class RegisterWordRequest(BaseModel):
    pattern: str
    canonical: str
    type: str = "custom"
    confidence: float = 1.0

@router.post("/wakewords/register")
async def wakewords_register(req: RegisterWordRequest):
    _wakewords.register_word(req.pattern, req.canonical, req.type, req.confidence)
    return {"registered": req.canonical, "pattern": req.pattern}


# ── Prompts ───────────────────────────────────────────────────────────────────
class RegisterPromptRequest(BaseModel):
    name: str
    text: str
    voice: Optional[str] = None
    rate:  Optional[str] = None
    pitch: Optional[str] = None

@router.get("/prompts")
async def prompts_list():
    return {"prompts": _prompts.list_prompts()}


@router.post("/prompts/register")
async def prompts_register(req: RegisterPromptRequest):
    prompt = _prompts.register(req.name, req.text, voice=req.voice, rate=req.rate, pitch=req.pitch)
    return prompt.to_dict()


@router.get("/prompts/{name}")
async def prompts_get(name: str, regen: bool = False):
    """Synthesize and return audio for a named prompt."""
    result = await _prompts.get(name, force_regen=regen)
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    return result


@router.delete("/prompts/{name}")
async def prompts_delete(name: str):
    removed = _prompts.unregister(name)
    return {"removed": removed, "name": name}


# ── Streaming WebSocket ───────────────────────────────────────────────────────
@router.websocket("/stream")
async def audio_stream(ws: WebSocket, language: Optional[str] = Query(None)):
    """
    Streaming ASR pipeline.
    Send audio chunks as {"type":"chunk","audio":"<base64>"} and flush with {"type":"flush"}.
    Transcripts and wake word events are sent back in real time.
    """
    await _stream.handle_connection(ws, language=language)
