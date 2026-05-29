from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    def load_dotenv() -> bool:
        return False

try:
    from groq import Groq
except Exception:  # pragma: no cover - optional at runtime
    Groq = None


load_dotenv()
app = FastAPI(title="ANDIE Backend")

MEMORY_PATH = Path(__file__).resolve().parent / "memory.json"
EVENT_LOG_PATH = Path(__file__).resolve().parent / "event_log.ndjson"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_memory() -> list[dict[str, Any]]:
    if not MEMORY_PATH.exists():
        return []
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _save_memory(memory: list[dict[str, Any]]) -> None:
    MEMORY_PATH.write_text(json.dumps(memory[-20:], indent=2), encoding="utf-8")


def _build_event_envelope(
    *,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    execution_id: str | None = None,
    workspace_id: str = "andie-default",
    correlation_id: str | None = None,
    sequence: int | None = None,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "timestamp": _utc_now(),
        "source": source,
        "version": 1,
        "workspace_id": workspace_id,
        "payload": payload,
    }

    if execution_id is not None:
        envelope["execution_id"] = execution_id
    if correlation_id is not None:
        envelope["correlation_id"] = correlation_id
    if sequence is not None:
        envelope["sequence"] = sequence

    # Compatibility alias for consumers that still expect `type`.
    envelope["type"] = event_type
    return envelope


class EventStore:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._next_seq = 1

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        execution_id: str | None = None,
        source: str = "runtime",
        workspace_id: str = "andie-default",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        event = _build_event_envelope(
            event_type=event_type,
            source=source,
            payload=payload,
            execution_id=execution_id,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            sequence=self._next_seq,
        )
        self._next_seq += 1
        self._events.append(event)
        with EVENT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
        return event

    def replay(self, execution_id: str) -> list[dict[str, Any]]:
        return [e for e in self._events if e.get("execution_id") == execution_id]

    def latest_seq(self) -> int:
        return self._next_seq - 1


EVENTS = EventStore()
ACTIVE_CONNECTIONS: set[WebSocket] = set()
WORKSPACE_SNAPSHOT: dict[str, Any] = {
    "workspace_id": "andie-default",
    "status": "healthy",
    "governance": {
        "band": "stable",
        "confidence": 1.0,
    },
    "updated_at": _utc_now(),
}


GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CLIENT = Groq(api_key=GROQ_API_KEY) if (Groq and GROQ_API_KEY) else None


class AgentRequest(BaseModel):
    input: str


class EventPublishRequest(BaseModel):
    type: str
    payload: dict[str, Any] = {}
    execution_id: str | None = None
    source: str = "runtime"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


async def _send_bootstrap(ws: WebSocket) -> None:
    conn_id = str(uuid4())

    ready_frame = _build_event_envelope(
        event_type="connection.ready",
        source="transport",
        payload={"connection_id": conn_id},
        sequence=EVENTS.latest_seq() + 1,
    )
    await ws.send_json(ready_frame)

    snapshot_frame = _build_event_envelope(
        event_type="workspace.snapshot",
        source="workspace",
        payload={"snapshot": WORKSPACE_SNAPSHOT},
        workspace_id=str(WORKSPACE_SNAPSHOT.get("workspace_id", "andie-default")),
        sequence=EVENTS.latest_seq() + 2,
    )
    await ws.send_json(snapshot_frame)


async def _stream_handler(ws: WebSocket) -> None:
    await ws.accept()
    ACTIVE_CONNECTIONS.add(ws)

    try:
        await _send_bootstrap(ws)

        while True:
            msg = await ws.receive_json()
            action = str(msg.get("action") or "").lower()
            if action == "ping":
                await ws.send_json(
                    _build_event_envelope(
                        event_type="connection.pong",
                        source="transport",
                        payload={"ok": True},
                        sequence=EVENTS.latest_seq() + 1,
                    )
                )
            elif action == "publish":
                ev_type = str(msg.get("type") or "workspace.event")
                payload = msg.get("payload") or {}
                execution_id = msg.get("execution_id")
                source = str(msg.get("source") or "runtime")
                workspace_id = str(msg.get("workspace_id") or WORKSPACE_SNAPSHOT.get("workspace_id", "andie-default"))
                correlation_id = msg.get("correlation_id")
                event = EVENTS.append(
                    ev_type,
                    payload,
                    execution_id=execution_id,
                    source=source,
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
                dead: list[WebSocket] = []
                for conn in ACTIVE_CONNECTIONS:
                    try:
                        await conn.send_json(event)
                    except Exception:
                        dead.append(conn)
                for conn in dead:
                    ACTIVE_CONNECTIONS.discard(conn)
            else:
                await ws.send_json(
                    _build_event_envelope(
                        event_type="connection.error",
                        source="transport",
                        payload={"message": f"unsupported action: {action}"},
                        sequence=EVENTS.latest_seq() + 1,
                    )
                )
    except WebSocketDisconnect:
        pass
    finally:
        ACTIVE_CONNECTIONS.discard(ws)


@app.get("/")
def home() -> dict[str, str]:
    return {"status": "ANDIE backend running"}


@app.get("/api/workspace/snapshot")
def workspace_snapshot() -> dict[str, Any]:
    return _build_event_envelope(
        event_type="workspace.snapshot",
        source="workspace",
        payload={"snapshot": WORKSPACE_SNAPSHOT},
        workspace_id=str(WORKSPACE_SNAPSHOT.get("workspace_id", "andie-default")),
        sequence=EVENTS.latest_seq() + 1,
    )


@app.post("/agents/run")
def run_agent(request: AgentRequest) -> dict[str, Any]:
    memory = _load_memory()
    memory.append({"role": "user", "content": request.input})

    if CLIENT is not None:
        response = CLIENT.chat.completions.create(
            messages=memory,
            model="llama-3.1-8b-instant",
        )
        reply = response.choices[0].message.content
    else:
        reply = f"[local-fallback] {request.input}"

    memory.append({"role": "assistant", "content": reply})
    _save_memory(memory)

    return {
        "result": reply,
        "memory_size": len(memory[-20:]),
    }


@app.post("/api/events")
async def publish_event(request: EventPublishRequest) -> dict[str, Any]:
    event = EVENTS.append(
        event_type=request.type,
        payload=request.payload,
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    dead: list[WebSocket] = []
    for conn in ACTIVE_CONNECTIONS:
        try:
            await conn.send_json(event)
        except Exception:
            dead.append(conn)
    for conn in dead:
        ACTIVE_CONNECTIONS.discard(conn)

    return {"status": "ok", "event": event}


@app.get("/api/replay/{execution_id}")
def replay_execution(execution_id: str) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "events": EVENTS.replay(execution_id),
    }


# Canonical streaming route
@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket) -> None:
    await _stream_handler(ws)


# Alias routes normalized to canonical bootstrap behavior
@app.websocket("/ws/events")
async def ws_events_alias(ws: WebSocket) -> None:
    await _stream_handler(ws)


@app.websocket("/ws/backlog")
async def ws_backlog_alias(ws: WebSocket) -> None:
    await _stream_handler(ws)
