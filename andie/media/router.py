"""
andie/media/router.py
FastAPI router for the Media Runtime layer.

Endpoints:
  POST /media/ocr/extract
  GET  /media/vision/events
  GET  /media/vision/summary
  POST /media/session/start
  POST /media/session/stop
  GET  /media/session/status
  WS   /media/ws   — live vision event stream
"""
from __future__ import annotations
import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from andie_backend.andie.media import session as _session
from andie_backend.andie.media import ocr as _ocr
from andie_backend.andie.media import vision_events as _vision

router = APIRouter(prefix="/media", tags=["media"])

# ── WebSocket manager ────────────────────────────────────────────────────────
class _WSManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self._connections = [c for c in self._connections if c is not ws]

    async def broadcast(self, data: dict):
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

_ws_manager = _WSManager()

# Register vision event subscriber → broadcast to all WS clients
def _on_vision_event(ev: dict):
    asyncio.get_event_loop().create_task(_ws_manager.broadcast({"type": "vision_event", "event": ev}))

_vision.subscribe(_on_vision_event)

# ── OCR ──────────────────────────────────────────────────────────────────────
class OCRRequest(BaseModel):
    image: str          # base64 or data URL
    mode: str = "screen"

@router.post("/ocr/extract")
async def ocr_extract(req: OCRRequest):
    """
    Extract text from a base64 image using Tesseract OCR.
    Detected heuristic vision events are pushed into the event bus.
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _ocr.extract, req.image, req.mode)

    # Push detected events into the vision bus
    if result.get("events"):
        _vision.push_events(result["events"], source="ocr")
        # Broadcast immediately to WS clients
        await _ws_manager.broadcast({
            "type": "ocr_complete",
            "lines": len(result.get("lines", [])),
            "events": result.get("events", []),
            "elapsed_ms": result.get("elapsed_ms"),
        })

    return result

# ── Vision Events ────────────────────────────────────────────────────────────
@router.get("/vision/events")
async def vision_events(limit: int = 50, severity: Optional[str] = None):
    return {"events": _vision.get_events(limit=limit, severity=severity)}

@router.get("/vision/summary")
async def vision_summary():
    return _vision.summary()

# ── Sessions ─────────────────────────────────────────────────────────────────
class SessionStartRequest(BaseModel):
    source: str  # "camera" | "screen" | "audio"

@router.post("/session/start")
async def session_start(req: SessionStartRequest):
    sess = _session.start_session(req.source)
    await _ws_manager.broadcast({"type": "session_started", "session": sess.to_dict()})
    return sess.to_dict()

@router.post("/session/stop")
async def session_stop(session_id: str):
    stopped = _session.stop_session(session_id)
    if stopped:
        await _ws_manager.broadcast({"type": "session_stopped", "session_id": session_id})
    return {"stopped": stopped, "session_id": session_id}

@router.get("/session/status")
async def session_status():
    active = _session.get_active()
    return {
        "active_count": len(active),
        "sessions": [s.to_dict() for s in active],
        "all": [s.to_dict() for s in _session.all_sessions()],
    }

# ── WebSocket stream ──────────────────────────────────────────────────────────
@router.websocket("/ws")
async def media_ws(ws: WebSocket):
    """
    Live stream of vision events, OCR results, and session state changes.
    Send { "ping": true } to keep alive.
    """
    await _ws_manager.connect(ws)
    # Send current summary on connect
    await ws.send_json({"type": "connected", "summary": _vision.summary()})
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_json(), timeout=30.0)
                if data.get("ping"):
                    await ws.send_json({"type": "pong", "ts": time.time()})
            except asyncio.TimeoutError:
                await ws.send_json({"type": "heartbeat", "ts": time.time()})
    except WebSocketDisconnect:
        _ws_manager.disconnect(ws)
