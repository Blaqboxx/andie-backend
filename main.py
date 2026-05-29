from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections import defaultdict
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


EVENT_FAMILIES: dict[str, set[str]] = {
    "objective": {
        "objective.created",
        "objective.updated",
        "objective.completed",
        "objective.blocked",
        "objective.unblocked",
        "objective.pressure",
        "objective.critical_path",
    },
    "governance": {
        "governance.escalation",
        "governance.cooldown",
        "governance.recovery",
        "governance.stability",
    },
    "execution": {
        "execution.started",
        "execution.completed",
        "execution.failed",
    },
    "trust": {
        "trust.recomputed",
        "trust.changed",
    },
    "recovery": {
        "rollback.started",
        "rollback.completed",
    },
}


def _is_event_type_valid(event_type: str) -> bool:
    if event_type in {
        "connection.ready",
        "connection.pong",
        "connection.error",
        "workspace.snapshot",
        "workspace.event",
        "timeline.transition",
        "telemetry.update",
        "lifecycle.transition",
        "telemetry.stabilization",
        "confidence.update",
        "rollback.marker",
    }:
        return True

    for family_events in EVENT_FAMILIES.values():
        if event_type in family_events:
            return True

    # Allow forward-compatible extension while enforcing event.family shape.
    return event_type.count(".") == 1 and all(part.strip() for part in event_type.split("."))


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
        if not _is_event_type_valid(event_type):
            raise ValueError(f"invalid event_type: {event_type}")

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

OBJECTIVES: dict[str, dict[str, Any]] = {}
OBJECTIVE_SIGNALS: dict[str, Any] = {
    "updated_at": _utc_now(),
    "blocked": {},
    "pressure": {},
    "critical_path": {},
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


class ObjectiveUpsertRequest(BaseModel):
    objective_id: str
    title: str
    priority: int = 1
    salience: float = 1.0
    depends_on: list[str] = []
    blocked_by: list[str] = []
    enables: list[str] = []
    status: str = "active"
    execution_id: str | None = None
    source: str = "objective-engine"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class ObjectiveStatusRequest(BaseModel):
    status: str
    execution_id: str | None = None
    source: str = "objective-engine"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


async def _fanout_event(event: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for conn in ACTIVE_CONNECTIONS:
        try:
            await conn.send_json(event)
        except Exception:
            dead.append(conn)
    for conn in dead:
        ACTIVE_CONNECTIONS.discard(conn)


def _normalize_objective(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "objective_id": str(obj.get("objective_id") or ""),
        "title": str(obj.get("title") or ""),
        "priority": max(0, int(obj.get("priority") or 0)),
        "salience": max(0.0, float(obj.get("salience") or 0.0)),
        "depends_on": [str(x) for x in (obj.get("depends_on") or []) if str(x)],
        "blocked_by": [str(x) for x in (obj.get("blocked_by") or []) if str(x)],
        "enables": [str(x) for x in (obj.get("enables") or []) if str(x)],
        "status": str(obj.get("status") or "active"),
    }


def _is_objective_active(obj: dict[str, Any]) -> bool:
    return str(obj.get("status") or "").lower() != "completed"


def _compute_critical_path(
    objective_id: str,
    enables_map: dict[str, list[str]],
    active_ids: set[str],
    visiting: set[str],
) -> int:
    if objective_id in visiting:
        return 0

    visiting.add(objective_id)
    longest = 0
    for nxt in enables_map.get(objective_id, []):
        if nxt in active_ids:
            longest = max(longest, _compute_critical_path(nxt, enables_map, active_ids, visiting))
    visiting.discard(objective_id)
    return 1 + longest


def _derive_objective_signals() -> dict[str, Any]:
    blocked: dict[str, bool] = {}
    critical_path: dict[str, int] = {}
    pressure: dict[str, float] = {}

    active_ids = {obj_id for obj_id, obj in OBJECTIVES.items() if _is_objective_active(obj)}
    enables_map: dict[str, list[str]] = defaultdict(list)
    outgoing_influence: dict[str, int] = defaultdict(int)

    for obj_id, obj in OBJECTIVES.items():
        for nxt in obj.get("enables", []):
            enables_map[obj_id].append(nxt)
            outgoing_influence[obj_id] += 1
        for ref in (obj.get("blocked_by", []) + obj.get("depends_on", [])):
            outgoing_influence[ref] += 1

    for obj_id, obj in OBJECTIVES.items():
        if not _is_objective_active(obj):
            blocked[obj_id] = False
            critical_path[obj_id] = 0
            pressure[obj_id] = 0.0
            continue

        blockers = set(obj.get("blocked_by", []) + obj.get("depends_on", []))
        is_blocked = any((ref in OBJECTIVES) and _is_objective_active(OBJECTIVES[ref]) for ref in blockers)
        blocked[obj_id] = is_blocked

        cp = _compute_critical_path(obj_id, enables_map, active_ids, set())
        critical_path[obj_id] = cp

        base = float(obj.get("priority", 0)) + float(obj.get("salience", 0.0))
        flow_bonus = float(outgoing_influence.get(obj_id, 0)) * 2.0
        critical_bonus = float(cp)
        blocked_penalty = -1.0 if is_blocked else 1.0
        pressure[obj_id] = round(base + flow_bonus + critical_bonus + blocked_penalty, 3)

    OBJECTIVE_SIGNALS.update(
        {
            "updated_at": _utc_now(),
            "blocked": blocked,
            "pressure": pressure,
            "critical_path": critical_path,
        }
    )
    return OBJECTIVE_SIGNALS


def _signal_delta_events(
    previous_blocked: dict[str, bool],
    execution_id: str | None,
    source: str,
    workspace_id: str,
    correlation_id: str | None,
) -> list[dict[str, Any]]:
    current = _derive_objective_signals()
    emitted: list[dict[str, Any]] = []

    for objective_id, is_blocked in current["blocked"].items():
        prev = previous_blocked.get(objective_id)
        if prev is None:
            continue
        if prev != is_blocked:
            emitted.append(
                EVENTS.append(
                    "objective.blocked" if is_blocked else "objective.unblocked",
                    {
                        "objective_id": objective_id,
                        "blocked": is_blocked,
                    },
                    execution_id=execution_id,
                    source=source,
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
            )

    emitted.append(
        EVENTS.append(
            "objective.pressure",
            {
                "ranking": sorted(
                    (
                        {
                            "objective_id": objective_id,
                            "pressure": pressure,
                        }
                        for objective_id, pressure in current["pressure"].items()
                    ),
                    key=lambda row: row["pressure"],
                    reverse=True,
                )
            },
            execution_id=execution_id,
            source=source,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
    )

    emitted.append(
        EVENTS.append(
            "objective.critical_path",
            {
                "critical_path": current["critical_path"],
            },
            execution_id=execution_id,
            source=source,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
    )
    return emitted


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
                await _fanout_event(event)
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

    await _fanout_event(event)

    return {"status": "ok", "event": event}


@app.get("/api/objectives/graph")
def get_objective_graph() -> dict[str, Any]:
    _derive_objective_signals()
    return {
        "objectives": list(OBJECTIVES.values()),
        "signals": OBJECTIVE_SIGNALS,
    }


@app.post("/api/objectives")
async def upsert_objective(request: ObjectiveUpsertRequest) -> dict[str, Any]:
    previous_blocked = dict(OBJECTIVE_SIGNALS.get("blocked", {}))
    existed = request.objective_id in OBJECTIVES

    OBJECTIVES[request.objective_id] = _normalize_objective(request.model_dump())

    lifecycle_event = EVENTS.append(
        "objective.updated" if existed else "objective.created",
        {"objective": OBJECTIVES[request.objective_id]},
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    await _fanout_event(lifecycle_event)

    emitted = _signal_delta_events(
        previous_blocked=previous_blocked,
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    for event in emitted:
        await _fanout_event(event)

    return {
        "status": "ok",
        "objective": OBJECTIVES[request.objective_id],
        "signals": OBJECTIVE_SIGNALS,
        "emitted_events": emitted,
    }


@app.post("/api/objectives/{objective_id}/status")
async def update_objective_status(objective_id: str, request: ObjectiveStatusRequest) -> dict[str, Any]:
    if objective_id not in OBJECTIVES:
        return {
            "status": "error",
            "message": f"unknown objective_id: {objective_id}",
        }

    previous_blocked = dict(OBJECTIVE_SIGNALS.get("blocked", {}))
    OBJECTIVES[objective_id]["status"] = request.status

    lifecycle_event = EVENTS.append(
        "objective.completed" if request.status.lower() == "completed" else "objective.updated",
        {
            "objective_id": objective_id,
            "status": request.status,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    await _fanout_event(lifecycle_event)

    emitted = _signal_delta_events(
        previous_blocked=previous_blocked,
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    for event in emitted:
        await _fanout_event(event)

    return {
        "status": "ok",
        "objective": OBJECTIVES[objective_id],
        "signals": OBJECTIVE_SIGNALS,
        "emitted_events": emitted,
    }


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
