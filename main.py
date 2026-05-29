from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections import defaultdict
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

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
        "governance.profile_applied",
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
    "agent": {
        "agent.assigned",
        "agent.completed",
        "agent.blocked",
        "agent.escalated",
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
    "objective_pressure_score": {},
    "critical_path": {},
}

TRUST_STATE: dict[str, Any] = {
    "score": 0.5,
    "updated_at": _utc_now(),
}

GOVERNANCE_STATE: dict[str, Any] = {
    "updated_at": _utc_now(),
    "band": "stable",
    "interrupt_sensitivity": 0.5,
    "escalation_readiness": 0.5,
    "cooldown_aggressiveness": 0.5,
    "posture_persistence": 0.5,
    "governance_attention": 0.5,
    "confidence": 1.0,
}

TRUST_STATES: dict[str, dict[str, Any]] = {
    "andie-default": TRUST_STATE,
}

GOVERNANCE_STATES: dict[str, dict[str, Any]] = {
    "andie-default": GOVERNANCE_STATE,
}

# Balanced is the frozen bootstrap baseline from Phase 2D coupling.
GOVERNANCE_PROFILES: dict[str, dict[str, float]] = {
    "balanced": {
        "interrupt_base": 1.0,
        "interrupt_trust_w": -0.65,
        "interrupt_failure_w": 0.15,
        "escalation_base": 0.2,
        "escalation_failure_w": 0.55,
        "escalation_blocked_w": 0.25,
        "cooldown_base": 0.75,
        "cooldown_critical_w": -0.3,
        "cooldown_pressure_w": -0.25,
        "cooldown_failure_w": 0.1,
        "posture_base": 0.2,
        "posture_critical_w": 0.45,
        "posture_pressure_w": 0.35,
        "posture_trust_w": 0.1,
        "attention_base": 0.15,
        "attention_blocked_w": 0.45,
        "attention_pressure_w": 0.4,
        "confidence_base": 1.0,
        "confidence_failure_w": -0.5,
        "confidence_blocked_w": -0.2,
        "band_escalated_threshold": 0.8,
        "band_warning_threshold": 0.5,
    },
    "conservative": {
        "interrupt_base": 1.0,
        "interrupt_trust_w": -0.5,
        "interrupt_failure_w": 0.25,
        "escalation_base": 0.35,
        "escalation_failure_w": 0.65,
        "escalation_blocked_w": 0.35,
        "cooldown_base": 0.55,
        "cooldown_critical_w": -0.2,
        "cooldown_pressure_w": -0.15,
        "cooldown_failure_w": 0.2,
        "posture_base": 0.25,
        "posture_critical_w": 0.5,
        "posture_pressure_w": 0.4,
        "posture_trust_w": 0.08,
        "attention_base": 0.2,
        "attention_blocked_w": 0.55,
        "attention_pressure_w": 0.45,
        "confidence_base": 1.0,
        "confidence_failure_w": -0.55,
        "confidence_blocked_w": -0.25,
        "band_escalated_threshold": 0.72,
        "band_warning_threshold": 0.42,
    },
    "aggressive": {
        "interrupt_base": 0.95,
        "interrupt_trust_w": -0.8,
        "interrupt_failure_w": 0.1,
        "escalation_base": 0.1,
        "escalation_failure_w": 0.45,
        "escalation_blocked_w": 0.2,
        "cooldown_base": 0.9,
        "cooldown_critical_w": -0.35,
        "cooldown_pressure_w": -0.3,
        "cooldown_failure_w": 0.05,
        "posture_base": 0.1,
        "posture_critical_w": 0.35,
        "posture_pressure_w": 0.25,
        "posture_trust_w": 0.2,
        "attention_base": 0.1,
        "attention_blocked_w": 0.35,
        "attention_pressure_w": 0.35,
        "confidence_base": 1.0,
        "confidence_failure_w": -0.45,
        "confidence_blocked_w": -0.15,
        "band_escalated_threshold": 0.9,
        "band_warning_threshold": 0.6,
    },
    "mission_critical": {
        "interrupt_base": 1.0,
        "interrupt_trust_w": -0.55,
        "interrupt_failure_w": 0.2,
        "escalation_base": 0.3,
        "escalation_failure_w": 0.6,
        "escalation_blocked_w": 0.35,
        "cooldown_base": 0.6,
        "cooldown_critical_w": -0.25,
        "cooldown_pressure_w": -0.2,
        "cooldown_failure_w": 0.15,
        "posture_base": 0.35,
        "posture_critical_w": 0.6,
        "posture_pressure_w": 0.5,
        "posture_trust_w": 0.05,
        "attention_base": 0.25,
        "attention_blocked_w": 0.6,
        "attention_pressure_w": 0.5,
        "confidence_base": 1.0,
        "confidence_failure_w": -0.6,
        "confidence_blocked_w": -0.25,
        "band_escalated_threshold": 0.68,
        "band_warning_threshold": 0.4,
    },
}

GOVERNANCE_PROFILE_STATE: dict[str, Any] = {
    "active": "balanced",
    "overrides": {},
    "updated_at": _utc_now(),
}

GOVERNANCE_PROFILE_BINDINGS: dict[str, dict[str, Any]] = {
    "andie-default": GOVERNANCE_PROFILE_STATE,
}

AGENT_ROLES: tuple[str, ...] = (
    "planner",
    "execution",
    "memory",
    "governance",
)

AGENT_TASKS_BY_WORKSPACE: dict[str, dict[str, dict[str, Any]]] = {
    "andie-default": {},
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
    depends_on: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    enables: list[str] = Field(default_factory=list)
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


class TrustRecomputeRequest(BaseModel):
    trust_score: float
    reason: str | None = None
    execution_id: str | None = None
    source: str = "trust-engine"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class GovernanceRecomputeRequest(BaseModel):
    execution_id: str | None = None
    source: str = "governance-engine"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class GovernanceProfileApplyRequest(BaseModel):
    profile: str
    overrides: dict[str, float] = Field(default_factory=dict)
    actor: str = "operator"
    reason: str = "manual profile selection"
    execution_id: str | None = None
    source: str = "governance-policy"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class AgentAssignmentRequest(BaseModel):
    task_id: str
    role: str
    objective_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "operator"
    reason: str = "manual assignment"
    execution_id: str | None = None
    source: str = "agent-orchestrator"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class AgentTaskStatusRequest(BaseModel):
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "operator"
    reason: str = "status update"
    execution_id: str | None = None
    source: str = "agent-orchestrator"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


def _get_trust_state(workspace_id: str) -> dict[str, Any]:
    if workspace_id not in TRUST_STATES:
        TRUST_STATES[workspace_id] = {
            "score": 0.5,
            "updated_at": _utc_now(),
        }
    return TRUST_STATES[workspace_id]


def _get_governance_state(workspace_id: str) -> dict[str, Any]:
    if workspace_id not in GOVERNANCE_STATES:
        GOVERNANCE_STATES[workspace_id] = {
            "updated_at": _utc_now(),
            "band": "stable",
            "interrupt_sensitivity": 0.5,
            "escalation_readiness": 0.5,
            "cooldown_aggressiveness": 0.5,
            "posture_persistence": 0.5,
            "governance_attention": 0.5,
            "confidence": 1.0,
            "profile": "balanced",
        }
    return GOVERNANCE_STATES[workspace_id]


def _get_governance_profile_binding(workspace_id: str) -> dict[str, Any]:
    if workspace_id not in GOVERNANCE_PROFILE_BINDINGS:
        GOVERNANCE_PROFILE_BINDINGS[workspace_id] = {
            "active": "balanced",
            "overrides": {},
            "updated_at": _utc_now(),
        }
    return GOVERNANCE_PROFILE_BINDINGS[workspace_id]


def _get_workspace_agent_tasks(workspace_id: str) -> dict[str, dict[str, Any]]:
    if workspace_id not in AGENT_TASKS_BY_WORKSPACE:
        AGENT_TASKS_BY_WORKSPACE[workspace_id] = {}
    return AGENT_TASKS_BY_WORKSPACE[workspace_id]


def _normalize_agent_role(role: str) -> str:
    role_value = role.strip().lower()
    if role_value not in AGENT_ROLES:
        raise ValueError(f"unknown agent role: {role}")
    return role_value


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
    pressure_score: dict[str, float] = {}

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

    max_pressure = max(pressure.values(), default=0.0)
    for obj_id, value in pressure.items():
        pressure_score[obj_id] = round((value / max_pressure), 3) if max_pressure > 0 else 0.0

    OBJECTIVE_SIGNALS.update(
        {
            "updated_at": _utc_now(),
            "blocked": blocked,
            "pressure": pressure,
            "objective_pressure_score": pressure_score,
            "critical_path": critical_path,
        }
    )
    return OBJECTIVE_SIGNALS


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _failure_pattern_score(execution_id: str | None = None) -> float:
    if execution_id:
        sample = EVENTS.replay(execution_id)
    else:
        sample = EVENTS._events[-100:]  # noqa: SLF001 - local in-process store

    failures = sum(1 for event in sample if event.get("event_type") == "execution.failed")
    return round(_clamp01(failures / 5.0), 3)


def _objective_context() -> dict[str, Any]:
    signals = _derive_objective_signals()
    blocked = signals.get("blocked", {})
    scores = signals.get("objective_pressure_score", {})
    active_count = sum(1 for obj in OBJECTIVES.values() if _is_objective_active(obj))
    blocked_count = sum(1 for _, is_blocked in blocked.items() if is_blocked)
    max_pressure_score = max(scores.values(), default=0.0)
    critical_active = any(path_len >= 2 for path_len in signals.get("critical_path", {}).values())

    return {
        "active_count": active_count,
        "blocked_count": blocked_count,
        "blocked_ratio": round((blocked_count / active_count), 3) if active_count > 0 else 0.0,
        "max_pressure_score": round(max_pressure_score, 3),
        "critical_path_active": critical_active,
    }


def _set_trust_score(
    trust_score: float,
    execution_id: str | None,
    source: str,
    workspace_id: str,
    correlation_id: str | None,
    reason: str | None = None,
) -> list[dict[str, Any]]:
    trust_state = _get_trust_state(workspace_id)
    previous = float(trust_state.get("score", 0.5))
    current = round(_clamp01(trust_score), 3)
    trust_state.update({"score": current, "updated_at": _utc_now()})

    events: list[dict[str, Any]] = [
        EVENTS.append(
            "trust.recomputed",
            {
                "trust_score": current,
                "previous": previous,
                "reason": reason,
            },
            execution_id=execution_id,
            source=source,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
    ]

    if abs(current - previous) >= 0.05:
        events.append(
            EVENTS.append(
                "trust.changed",
                {
                    "previous": previous,
                    "current": current,
                    "delta": round(current - previous, 3),
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    return events


def _recompute_governance_state(
    execution_id: str | None,
    source: str,
    workspace_id: str,
    correlation_id: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trust_state = _get_trust_state(workspace_id)
    governance_state = _get_governance_state(workspace_id)
    profile_binding = _get_governance_profile_binding(workspace_id)

    trust_score = float(trust_state.get("score", 0.5))
    failure_score = _failure_pattern_score(execution_id=execution_id)
    objective_ctx = _objective_context()

    prev_band = str(governance_state.get("band", "stable"))
    prev_cooldown = float(governance_state.get("cooldown_aggressiveness", 0.5))

    blocked_ratio = float(objective_ctx["blocked_ratio"])
    max_pressure_score = float(objective_ctx["max_pressure_score"])
    critical_active = 1.0 if bool(objective_ctx["critical_path_active"]) else 0.0

    profile_name = str(profile_binding.get("active") or "balanced")
    profile = dict(GOVERNANCE_PROFILES.get(profile_name, GOVERNANCE_PROFILES["balanced"]))
    for key, value in (profile_binding.get("overrides") or {}).items():
        if key in profile:
            profile[key] = float(value)

    interrupt_sensitivity = _clamp01(
        profile["interrupt_base"]
        + (profile["interrupt_trust_w"] * trust_score)
        + (profile["interrupt_failure_w"] * failure_score)
    )
    escalation_readiness = _clamp01(
        profile["escalation_base"]
        + (profile["escalation_failure_w"] * failure_score)
        + (profile["escalation_blocked_w"] * blocked_ratio)
    )
    cooldown_aggressiveness = _clamp01(
        profile["cooldown_base"]
        + (profile["cooldown_critical_w"] * critical_active)
        + (profile["cooldown_pressure_w"] * max_pressure_score)
        + (profile["cooldown_failure_w"] * failure_score)
    )
    posture_persistence = _clamp01(
        profile["posture_base"]
        + (profile["posture_critical_w"] * critical_active)
        + (profile["posture_pressure_w"] * max_pressure_score)
        + (profile["posture_trust_w"] * trust_score)
    )
    governance_attention = _clamp01(
        profile["attention_base"]
        + (profile["attention_blocked_w"] * blocked_ratio)
        + (profile["attention_pressure_w"] * max_pressure_score)
    )
    confidence = _clamp01(
        profile["confidence_base"]
        + (profile["confidence_failure_w"] * failure_score)
        + (profile["confidence_blocked_w"] * blocked_ratio)
    )

    if escalation_readiness >= profile["band_escalated_threshold"]:
        band = "escalated"
    elif escalation_readiness >= profile["band_warning_threshold"]:
        band = "warning"
    else:
        band = "stable"

    governance_state.update(
        {
            "updated_at": _utc_now(),
            "band": band,
            "interrupt_sensitivity": round(interrupt_sensitivity, 3),
            "escalation_readiness": round(escalation_readiness, 3),
            "cooldown_aggressiveness": round(cooldown_aggressiveness, 3),
            "posture_persistence": round(posture_persistence, 3),
            "governance_attention": round(governance_attention, 3),
            "confidence": round(confidence, 3),
            "profile": profile_name,
            "inputs": {
                "trust_score": round(trust_score, 3),
                "failure_pattern_score": round(failure_score, 3),
                "objective_context": objective_ctx,
                "profile": profile_name,
            },
        }
    )

    WORKSPACE_SNAPSHOT["governance"] = {
        "band": band,
        "confidence": round(confidence, 3),
        "posture_persistence": round(posture_persistence, 3),
        "cooldown_aggressiveness": round(cooldown_aggressiveness, 3),
        "interrupt_sensitivity": round(interrupt_sensitivity, 3),
        "governance_attention": round(governance_attention, 3),
        "profile": profile_name,
        "updated_at": governance_state["updated_at"],
    }

    events: list[dict[str, Any]] = [
        EVENTS.append(
            "governance.stability",
            {
                "governance": governance_state,
                "workspace_id": workspace_id,
            },
            execution_id=execution_id,
            source=source,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
    ]

    if band == "escalated" and prev_band != "escalated":
        events.append(
            EVENTS.append(
                "governance.escalation",
                {
                    "from_band": prev_band,
                    "to_band": band,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )
    elif prev_band == "escalated" and band in {"warning", "stable"}:
        events.append(
            EVENTS.append(
                "governance.recovery",
                {
                    "from_band": prev_band,
                    "to_band": band,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    if cooldown_aggressiveness < (prev_cooldown - 0.1):
        events.append(
            EVENTS.append(
                "governance.cooldown",
                {
                    "cooldown_aggressiveness": round(cooldown_aggressiveness, 3),
                    "previous": round(prev_cooldown, 3),
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    WORKSPACE_SNAPSHOT["updated_at"] = _utc_now()
    return governance_state, events


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
                            "objective_pressure_score": current["objective_pressure_score"].get(objective_id, 0.0),
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


def _apply_governance_profile(
    profile_name: str,
    overrides: dict[str, float],
    actor: str,
    reason: str,
    execution_id: str | None,
    source: str,
    workspace_id: str,
    correlation_id: str | None,
) -> dict[str, Any]:
    if profile_name not in GOVERNANCE_PROFILES:
        raise ValueError(f"unknown governance profile: {profile_name}")

    known_keys = set(GOVERNANCE_PROFILES["balanced"].keys())
    sanitized: dict[str, float] = {}
    for key, value in overrides.items():
        if key in known_keys:
            sanitized[key] = float(value)

    profile_binding = _get_governance_profile_binding(workspace_id)
    previous_profile = str(profile_binding.get("active") or "balanced")
    profile_binding.update(
        {
            "active": profile_name,
            "overrides": sanitized,
            "updated_at": _utc_now(),
        }
    )

    return EVENTS.append(
        "governance.profile_applied",
        {
            "previous_profile": previous_profile,
            "profile": profile_name,
            "overrides": sanitized,
            "workspace_id": workspace_id,
            "actor": actor,
            "reason": reason,
            "correlation_id": correlation_id,
        },
        execution_id=execution_id,
        source=source,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
    )


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


@app.get("/api/governance/state")
def get_governance_state(workspace_id: str = "andie-default") -> dict[str, Any]:
    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "governance": _get_governance_state(workspace_id),
        "trust": _get_trust_state(workspace_id),
        "profile": _get_governance_profile_binding(workspace_id),
    }


@app.get("/api/governance/profiles")
def list_governance_profiles(workspace_id: str = "andie-default") -> dict[str, Any]:
    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "active_profile": _get_governance_profile_binding(workspace_id),
        "profiles": GOVERNANCE_PROFILES,
    }


@app.post("/api/governance/profile/apply")
async def apply_governance_profile(request: GovernanceProfileApplyRequest) -> dict[str, Any]:
    try:
        profile_event = _apply_governance_profile(
            profile_name=request.profile,
            overrides=request.overrides,
            actor=request.actor,
            reason=request.reason,
            execution_id=request.execution_id,
            source=request.source,
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _fanout_event(profile_event)

    governance, governance_events = _recompute_governance_state(
        execution_id=request.execution_id,
        source="governance-engine",
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    for event in governance_events:
        await _fanout_event(event)

    return {
        "status": "ok",
        "profile": _get_governance_profile_binding(request.workspace_id),
        "governance": governance,
        "emitted_events": [profile_event] + governance_events,
    }


@app.get("/api/agents/roles")
def list_agent_roles() -> dict[str, Any]:
    return {
        "status": "ok",
        "roles": list(AGENT_ROLES),
    }


@app.get("/api/agents/tasks")
def list_agent_tasks(workspace_id: str = "andie-default") -> dict[str, Any]:
    tasks = _get_workspace_agent_tasks(workspace_id)
    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "tasks": list(tasks.values()),
    }


@app.post("/api/agents/assign")
async def assign_agent_task(request: AgentAssignmentRequest) -> dict[str, Any]:
    try:
        role = _normalize_agent_role(request.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workspace_tasks = _get_workspace_agent_tasks(request.workspace_id)
    now = _utc_now()
    task = {
        "task_id": request.task_id,
        "role": role,
        "objective_id": request.objective_id,
        "status": "assigned",
        "payload": request.payload,
        "actor": request.actor,
        "reason": request.reason,
        "workspace_id": request.workspace_id,
        "created_at": workspace_tasks.get(request.task_id, {}).get("created_at", now),
        "updated_at": now,
    }
    workspace_tasks[request.task_id] = task

    event = EVENTS.append(
        "agent.assigned",
        {
            "task": task,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    await _fanout_event(event)

    return {
        "status": "ok",
        "task": task,
        "event": event,
    }


@app.post("/api/agents/{task_id}/status")
async def update_agent_task_status(task_id: str, request: AgentTaskStatusRequest) -> dict[str, Any]:
    workspace_tasks = _get_workspace_agent_tasks(request.workspace_id)
    if task_id not in workspace_tasks:
        raise HTTPException(status_code=404, detail=f"unknown task_id: {task_id}")

    event_map = {
        "completed": "agent.completed",
        "blocked": "agent.blocked",
        "escalated": "agent.escalated",
    }
    status_value = request.status.strip().lower()
    if status_value not in event_map:
        raise HTTPException(status_code=400, detail=f"unsupported agent task status: {request.status}")

    task = workspace_tasks[task_id]
    task.update(
        {
            "status": status_value,
            "actor": request.actor,
            "reason": request.reason,
            "updated_at": _utc_now(),
            "last_payload": request.payload,
        }
    )

    event = EVENTS.append(
        event_map[status_value],
        {
            "task_id": task_id,
            "role": task.get("role"),
            "objective_id": task.get("objective_id"),
            "status": status_value,
            "payload": request.payload,
            "actor": request.actor,
            "reason": request.reason,
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    await _fanout_event(event)

    return {
        "status": "ok",
        "task": task,
        "event": event,
    }


@app.post("/api/trust/recompute")
async def recompute_trust(request: TrustRecomputeRequest) -> dict[str, Any]:
    trust_events = _set_trust_score(
        trust_score=request.trust_score,
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
        reason=request.reason,
    )
    for event in trust_events:
        await _fanout_event(event)

    governance, governance_events = _recompute_governance_state(
        execution_id=request.execution_id,
        source="governance-engine",
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    for event in governance_events:
        await _fanout_event(event)

    return {
        "status": "ok",
        "trust": _get_trust_state(request.workspace_id),
        "governance": governance,
        "emitted_events": trust_events + governance_events,
    }


@app.post("/api/governance/recompute")
async def recompute_governance(request: GovernanceRecomputeRequest) -> dict[str, Any]:
    governance, governance_events = _recompute_governance_state(
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    for event in governance_events:
        await _fanout_event(event)

    return {
        "status": "ok",
        "governance": governance,
        "emitted_events": governance_events,
    }


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

    governance, governance_events = _recompute_governance_state(
        execution_id=request.execution_id,
        source="governance-engine",
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    for event in governance_events:
        await _fanout_event(event)

    return {
        "status": "ok",
        "objective": OBJECTIVES[request.objective_id],
        "signals": OBJECTIVE_SIGNALS,
        "governance": governance,
        "emitted_events": emitted + governance_events,
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

    governance, governance_events = _recompute_governance_state(
        execution_id=request.execution_id,
        source="governance-engine",
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    for event in governance_events:
        await _fanout_event(event)

    return {
        "status": "ok",
        "objective": OBJECTIVES[objective_id],
        "signals": OBJECTIVE_SIGNALS,
        "governance": governance,
        "emitted_events": emitted + governance_events,
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
