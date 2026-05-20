"""
andie/audio/streaming/stream.py
WebSocket audio streaming pipeline.

Clients send base64-encoded audio chunks over WebSocket.
Chunks are accumulated until a silence/flush signal, then sent to ASR.
Transcripts and detected events are broadcast back to all connected clients.

Protocol (client → server):
  {"type": "chunk",   "audio": "<base64>", "mime": "audio/webm"}
  {"type": "flush"}                    ← force transcription now
  {"type": "ping"}

Protocol (server → client):
  {"type": "transcript", "text": "...", "events": [...], "elapsed_ms": N}
  {"type": "event",      "event": {...}}
  {"type": "wakeword",   "hits": [...]}
  {"type": "pong",       "ts": N}
  {"type": "error",      "detail": "..."}
  {"type": "heartbeat",  "ts": N}
"""
from __future__ import annotations

import asyncio
import base64
import io
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from andie_backend.andie.audio.asr.engine import transcribe
from andie_backend.andie.audio.wakewords.detector import check_transcript
from andie_backend.andie.audio.events.bus import push_events

# ── Connection manager ────────────────────────────────────────────────────────
class AudioStreamManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c is not ws]

    async def broadcast(self, data: dict) -> None:
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


manager = AudioStreamManager()


# ── Per-connection handler ────────────────────────────────────────────────────
async def handle_connection(ws: WebSocket, language: Optional[str] = None) -> None:
    """
    Manage a single streaming audio connection.
    Accumulates chunks until flush or AUTO_FLUSH_BYTES, then transcribes.
    """
    await manager.connect(ws)
    await ws.send_json({"type": "connected", "ts": time.time()})

    chunk_buf: list[bytes] = []
    AUTO_FLUSH_BYTES = 512 * 1024  # 512 KB
    accumulated = 0

    async def _flush() -> None:
        nonlocal chunk_buf, accumulated
        if not chunk_buf:
            return
        combined = b"".join(chunk_buf)
        chunk_buf = []
        accumulated = 0

        audio_b64 = base64.b64encode(combined).decode()
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, transcribe, audio_b64, language)
            text = result.get("text", "").strip()
            events = result.get("events", [])

            if text:
                # Wake word detection
                ww_hits = check_transcript(text)

                # Push to event bus
                if events:
                    push_events(events, source="asr-stream")
                if ww_hits:
                    for hit in ww_hits:
                        push_events([{
                            "type": f"wakeword:{hit['word']}",
                            "value": hit["word"],
                            "severity": "info" if hit["type"] != "wake" else "medium",
                            "meta": hit,
                        }], source="wakeword-detector")

                await ws.send_json({
                    "type":       "transcript",
                    "text":       text,
                    "segments":   result.get("segments", []),
                    "language":   result.get("language"),
                    "events":     events,
                    "wakewords":  ww_hits,
                    "elapsed_ms": result.get("elapsed_ms"),
                })

                if ww_hits:
                    await ws.send_json({"type": "wakeword", "hits": ww_hits})

        except Exception as exc:
            await ws.send_json({"type": "error", "detail": str(exc)[:200]})

    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=30.0)
                msg_type = msg.get("type", "")

                if msg_type == "chunk":
                    raw_b64 = msg.get("audio", "")
                    if "," in raw_b64:
                        raw_b64 = raw_b64.split(",", 1)[1]
                    raw = base64.b64decode(raw_b64)
                    chunk_buf.append(raw)
                    accumulated += len(raw)
                    if accumulated >= AUTO_FLUSH_BYTES:
                        await _flush()

                elif msg_type == "flush":
                    await _flush()

                elif msg_type == "ping":
                    await ws.send_json({"type": "pong", "ts": time.time()})

            except asyncio.TimeoutError:
                await ws.send_json({"type": "heartbeat", "ts": time.time()})

    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)
