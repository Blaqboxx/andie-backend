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
        "agent.decision_context",
        "agent.assignment_strategy",
        "agent.collaboration_plan",
        "agent.workflow_started",
        "agent.workflow_updated",
        "agent.workflow_health",
        "agent.workflow_blocked",
        "agent.workflow_replanned",
        "agent.workflow_completed",
        "agent.delegated",
        "agent.review_requested",
        "agent.review_completed",
        "agent.consensus_started",
        "agent.consensus_reached",
        "agent.consensus_failed",
        "agent.supervisor_invoked",
        "agent.supervisor_replanned",
        "agent.supervisor_redelegated",
        "agent.supervisor_resumed",
        "agent.supervisor_prioritized",
        "agent.supervisor_preempted",
        "agent.supervisor_reallocated",
        "agent.supervisor_transferred",
        "agent.supervisor_aged",
        "agent.supervisor_boosted",
        "agent.supervisor_starvation_detected",
        "agent.supervisor_fairness_applied",
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

AGENT_WORKFLOWS_BY_WORKSPACE: dict[str, dict[str, dict[str, Any]]] = {
    "andie-default": {},
}

SUPERVISOR_RUNTIME_BY_WORKSPACE: dict[str, dict[str, Any]] = {
    "andie-default": {
        "available_slots": 1,
        "active_workflows": [],
        "updated_at": _utc_now(),
    }
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


class AgentWorkflowUpdateRequest(BaseModel):
    status: str
    step_role: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = "workflow update"
    actor: str = "orchestrator"
    execution_id: str | None = None
    source: str = "agent-orchestrator"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class AgentWorkflowDelegationRequest(BaseModel):
    from_role: str
    to_role: str
    reason: str = "delegation"
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "orchestrator"
    execution_id: str | None = None
    source: str = "agent-orchestrator"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class AgentWorkflowReviewRequest(BaseModel):
    reviewer_role: str = "governance"
    status: str = "requested"
    reason: str = "review chain"
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "orchestrator"
    execution_id: str | None = None
    source: str = "agent-orchestrator"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class AgentWorkflowConsensusRequest(BaseModel):
    participants: list[str] = Field(default_factory=list)
    reached: bool
    resolution: str | None = None
    reason: str = "consensus"
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "orchestrator"
    execution_id: str | None = None
    source: str = "agent-orchestrator"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class AgentWorkflowSupervisionRequest(BaseModel):
    trigger: str = "manual"
    reason: str = "supervisor review"
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "workflow-supervisor"
    execution_id: str | None = None
    source: str = "workflow-supervisor"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class AgentSupervisorArbitrationRequest(BaseModel):
    available_slots: int = 1
    fairness_window: int = 3
    starvation_threshold: int = 3
    trigger: str = "manual"
    reason: str = "cross-workflow arbitration"
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "workflow-supervisor"
    execution_id: str | None = None
    source: str = "workflow-supervisor"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class AgentArbitrationRequest(BaseModel):
    task_id: str
    objective_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "orchestrator"
    reason: str = "objective arbitration"
    operator_forced_role: str | None = None
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


def _get_workspace_workflows(workspace_id: str) -> dict[str, dict[str, Any]]:
    if workspace_id not in AGENT_WORKFLOWS_BY_WORKSPACE:
        AGENT_WORKFLOWS_BY_WORKSPACE[workspace_id] = {}
    return AGENT_WORKFLOWS_BY_WORKSPACE[workspace_id]


def _get_supervisor_runtime(workspace_id: str) -> dict[str, Any]:
    if workspace_id not in SUPERVISOR_RUNTIME_BY_WORKSPACE:
        SUPERVISOR_RUNTIME_BY_WORKSPACE[workspace_id] = {
            "available_slots": 1,
            "fairness_window": 3,
            "starvation_threshold": 3,
            "cycle": 0,
            "active_workflows": [],
            "updated_at": _utc_now(),
        }
    return SUPERVISOR_RUNTIME_BY_WORKSPACE[workspace_id]


def _normalize_agent_role(role: str) -> str:
    role_value = role.strip().lower()
    if role_value not in AGENT_ROLES:
        raise ValueError(f"unknown agent role: {role}")
    return role_value


def _governance_assignment_constraints(workspace_id: str) -> dict[str, Any]:
    governance_state = _get_governance_state(workspace_id)
    profile = str(_get_governance_profile_binding(workspace_id).get("active", "balanced"))
    band = str(governance_state.get("band", "stable"))

    constraints: dict[str, Any] = {
        "governance_band": band,
        "workspace_profile": profile,
        "preferred_role": "execution",
        "requires_governance_review": False,
    }

    if band == "warning":
        constraints["preferred_role"] = "planner"
        constraints["requires_governance_review"] = True
    elif band == "escalated":
        constraints["preferred_role"] = "governance"
        constraints["requires_governance_review"] = True

    if profile == "mission_critical" and band != "escalated":
        constraints["preferred_role"] = "planner"
        constraints["requires_governance_review"] = True
    elif profile == "aggressive" and band == "stable":
        constraints["preferred_role"] = "execution"

    return constraints


def _select_agent_strategy_and_role(
    workspace_id: str,
    objective_id: str | None,
    operator_forced_role: str | None,
) -> tuple[str, str, dict[str, Any]]:
    constraints = _governance_assignment_constraints(workspace_id)
    signals = _derive_objective_signals()
    pressure_score = float((signals.get("objective_pressure_score") or {}).get(objective_id or "", 0.0))
    trust_score = float(_get_trust_state(workspace_id).get("score", 0.5))
    governance_band = str(constraints.get("governance_band", "stable"))
    profile = str(constraints.get("workspace_profile", "balanced"))

    if operator_forced_role is not None:
        role = _normalize_agent_role(operator_forced_role)
        return (
            "operator_forced",
            role,
            {
                "pressure_score": round(pressure_score, 3),
                "trust_score": round(trust_score, 3),
                "governance_band": governance_band,
                "workspace_profile": profile,
                "constraints": constraints,
                "operator_forced_role": role,
            },
        )

    if governance_band == "escalated" or (profile == "mission_critical" and pressure_score >= 0.6):
        return (
            "governance_directed",
            str(constraints.get("preferred_role", "governance")),
            {
                "pressure_score": round(pressure_score, 3),
                "trust_score": round(trust_score, 3),
                "governance_band": governance_band,
                "workspace_profile": profile,
                "constraints": constraints,
            },
        )

    if trust_score < 0.4:
        return (
            "trust_based",
            "memory",
            {
                "pressure_score": round(pressure_score, 3),
                "trust_score": round(trust_score, 3),
                "governance_band": governance_band,
                "workspace_profile": profile,
                "constraints": constraints,
            },
        )

    if pressure_score >= 0.75:
        role = "execution" if profile == "aggressive" else str(constraints.get("preferred_role", "planner"))
        return (
            "pressure_based",
            role,
            {
                "pressure_score": round(pressure_score, 3),
                "trust_score": round(trust_score, 3),
                "governance_band": governance_band,
                "workspace_profile": profile,
                "constraints": constraints,
            },
        )

    return (
        "governance_directed" if governance_band == "warning" else "pressure_based",
        str(constraints.get("preferred_role", "planner")),
        {
            "pressure_score": round(pressure_score, 3),
            "trust_score": round(trust_score, 3),
            "governance_band": governance_band,
            "workspace_profile": profile,
            "constraints": constraints,
        },
    )


def _create_assigned_task(
    task_id: str,
    role: str,
    objective_id: str | None,
    payload: dict[str, Any],
    actor: str,
    reason: str,
    workspace_id: str,
    strategy: str,
) -> dict[str, Any]:
    workspace_tasks = _get_workspace_agent_tasks(workspace_id)
    now = _utc_now()
    task = {
        "task_id": task_id,
        "role": role,
        "objective_id": objective_id,
        "status": "assigned",
        "payload": payload,
        "actor": actor,
        "reason": reason,
        "strategy": strategy,
        "workspace_id": workspace_id,
        "created_at": workspace_tasks.get(task_id, {}).get("created_at", now),
        "updated_at": now,
    }
    workspace_tasks[task_id] = task
    return task


def _build_collaboration_plan(
    selected_role: str,
    strategy: str,
    strategy_inputs: dict[str, Any],
) -> tuple[list[str], str]:
    pressure_score = float(strategy_inputs.get("pressure_score") or 0.0)
    trust_score = float(strategy_inputs.get("trust_score") or 0.5)
    governance_band = str(strategy_inputs.get("governance_band") or "stable")
    workspace_profile = str(strategy_inputs.get("workspace_profile") or "balanced")

    if strategy == "operator_forced":
        return ([selected_role], "operator_forced_single_role")

    if governance_band == "escalated":
        return (["governance", "planner", "execution"], "escalated_governance_mandatory")

    if strategy == "trust_based" or trust_score < 0.4:
        return (["memory", "planner", "execution"], "trust_recovery_knowledge_gap")

    if workspace_profile == "mission_critical" and pressure_score >= 0.6:
        return (["planner", "governance", "execution"], "mission_critical_high_pressure")

    if strategy == "governance_directed":
        return (["planner", "governance", "execution"], "governance_directed_review_chain")

    if strategy == "pressure_based" and pressure_score >= 0.75:
        if workspace_profile == "aggressive":
            return (["execution", "planner"], "aggressive_high_pressure_fast_path")
        return (["planner", "execution"], "high_pressure_two_stage")

    return ([selected_role], "single_role_default")


def _workflow_pressure_score(
    *,
    workspace_id: str,
    objective_id: str | None,
    blocked_steps: int,
) -> float:
    signals = _derive_objective_signals()
    objective_pressure = float((signals.get("objective_pressure_score") or {}).get(objective_id or "", 0.0))
    trust_score = float(_get_trust_state(workspace_id).get("score", 0.5))
    governance_band = str(_get_governance_state(workspace_id).get("band", "stable"))

    band_weight = {
        "stable": 0.2,
        "warning": 0.5,
        "escalated": 0.8,
    }.get(governance_band, 0.2)

    blocked_weight = min(1.0, blocked_steps / 3.0)
    trust_penalty = 1.0 - trust_score

    score = (0.4 * objective_pressure) + (0.25 * blocked_weight) + (0.2 * band_weight) + (0.15 * trust_penalty)
    return round(_clamp01(score), 3)


def _build_workflow(
    *,
    task_id: str,
    objective_id: str | None,
    workflow_roles: list[str],
    reason: str,
    selected_strategy: str,
    selected_role: str,
    workspace_id: str,
) -> dict[str, Any]:
    now = _utc_now()
    workflow = {
        "workflow_id": task_id,
        "task_id": task_id,
        "objective_id": objective_id,
        "workflow": workflow_roles,
        "reason": reason,
        "selected_strategy": selected_strategy,
        "selected_role": selected_role,
        "status": "started",
        "current_step_index": 0,
        "blocked_steps": 0,
        "replan_count": 0,
        "supervisor_actions": 0,
        "workflow_age": 0,
        "workflow_wait_time": 0,
        "priority_boost": 0.0,
        "starvation_score": 0.0,
        "scheduler_cycle_last_scheduled": 0,
        "history": [
            {
                "at": now,
                "status": "started",
            }
        ],
        "created_at": now,
        "updated_at": now,
    }
    workflow["workflow_pressure_score"] = _workflow_pressure_score(
        workspace_id=workspace_id,
        objective_id=objective_id,
        blocked_steps=0,
    )
    return workflow


def _workflow_health_payload(workspace_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    governance_band = str(_get_governance_state(workspace_id).get("band", "stable"))
    return {
        "workflow_id": workflow.get("workflow_id"),
        "task_id": workflow.get("task_id"),
        "objective_id": workflow.get("objective_id"),
        "workflow_pressure_score": workflow.get("workflow_pressure_score"),
        "blocked_steps": int(workflow.get("blocked_steps", 0)),
        "replan_count": int(workflow.get("replan_count", 0)),
        "supervisor_actions": int(workflow.get("supervisor_actions", 0)),
        "priority": float(workflow.get("priority", 0.0)),
        "workflow_age": int(workflow.get("workflow_age", 0)),
        "workflow_wait_time": int(workflow.get("workflow_wait_time", 0)),
        "priority_boost": float(workflow.get("priority_boost", 0.0)),
        "starvation_score": float(workflow.get("starvation_score", 0.0)),
        "governance_band": governance_band,
        "status": workflow.get("status"),
    }


def _replan_workflow_roles(workflow: dict[str, Any], profile: str) -> tuple[list[str], str]:
    current_roles = [str(role) for role in workflow.get("workflow", [])]
    if not current_roles:
        return (["planner", "execution"], "empty_workflow_recovery")

    if "memory" not in current_roles:
        replanned = ["memory"] + current_roles
        return (replanned, "blocked_replan_memory_injection")

    if profile == "mission_critical" and "governance" not in current_roles:
        replanned = ["governance"] + current_roles
        return (replanned, "blocked_replan_governance_injection")

    return (current_roles + ["planner"], "blocked_replan_planner_tail")


def _ensure_role_front(workflow_roles: list[str], role: str) -> list[str]:
    normalized = [str(r) for r in workflow_roles if str(r) != role]
    return [role] + normalized


def _supervisor_apply(
    *,
    workflow: dict[str, Any],
    workspace_id: str,
    trigger: str,
) -> tuple[str, str]:
    profile = str(_get_governance_profile_binding(workspace_id).get("active", "balanced"))
    governance_band = str(_get_governance_state(workspace_id).get("band", "stable"))
    pressure = float(workflow.get("workflow_pressure_score", 0.0))
    workflow_roles = [str(r) for r in workflow.get("workflow", [])]

    if governance_band == "escalated" or trigger == "consensus_failed":
        workflow["workflow"] = _ensure_role_front(workflow_roles, "governance")
        workflow["status"] = "supervisor_replanned"
        return ("agent.supervisor_replanned", "supervisor_escalation_gate")

    if profile == "mission_critical" and pressure >= 0.6:
        workflow["workflow"] = _ensure_role_front(_ensure_role_front(workflow_roles, "planner"), "governance")
        workflow["status"] = "supervisor_replanned"
        return ("agent.supervisor_replanned", "supervisor_mission_critical_chain")

    if trigger in {"workflow_blocked", "blocked"} or int(workflow.get("blocked_steps", 0)) >= 2:
        workflow["workflow"] = _ensure_role_front(workflow_roles, "memory")
        workflow["status"] = "supervisor_redelegated"
        return ("agent.supervisor_redelegated", "supervisor_blocked_memory_redelegation")

    if pressure >= 0.85:
        workflow["workflow"] = _ensure_role_front(workflow_roles, "planner")
        workflow["status"] = "supervisor_replanned"
        return ("agent.supervisor_replanned", "supervisor_high_pressure_replan")

    workflow["status"] = "supervisor_resumed"
    return ("agent.supervisor_resumed", "supervisor_continue_current_plan")


def _workflow_priority_score(workspace_id: str, workflow: dict[str, Any]) -> float:
    governance_band = str(_get_governance_state(workspace_id).get("band", "stable"))
    band_weight = {
        "stable": 0.2,
        "warning": 0.5,
        "escalated": 0.8,
    }.get(governance_band, 0.2)

    base_pressure = float(workflow.get("workflow_pressure_score", 0.0))
    blocked = min(1.0, float(int(workflow.get("blocked_steps", 0))) / 3.0)
    replans = min(1.0, float(int(workflow.get("replan_count", 0))) / 5.0)
    supervisor_actions = min(1.0, float(int(workflow.get("supervisor_actions", 0))) / 5.0)

    score = (0.5 * base_pressure) + (0.2 * blocked) + (0.15 * replans) + (0.1 * band_weight) + (0.05 * supervisor_actions)
    return round(_clamp01(score), 3)


def _run_supervisor_arbitration(
    *,
    workspace_id: str,
    available_slots: int,
    fairness_window: int,
    starvation_threshold: int,
    execution_id: str | None,
    source: str,
    correlation_id: str | None,
    trigger: str,
    reason: str,
    actor: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    runtime = _get_supervisor_runtime(workspace_id)
    runtime["available_slots"] = max(1, int(available_slots))
    runtime["fairness_window"] = max(1, int(fairness_window))
    runtime["starvation_threshold"] = max(1, int(starvation_threshold))
    runtime["cycle"] = int(runtime.get("cycle", 0)) + 1
    workflows = _get_workspace_workflows(workspace_id)
    cycle = int(runtime["cycle"])

    ranked: list[tuple[str, float]] = []
    fairness_candidates: list[tuple[str, int]] = []
    for workflow_id, workflow in workflows.items():
        if str(workflow.get("status", "")).lower() == "completed":
            continue

        wait_time = int(workflow.get("workflow_wait_time", 0))
        age = int(workflow.get("workflow_age", 0))
        last_scheduled = int(workflow.get("scheduler_cycle_last_scheduled", 0))
        if last_scheduled >= cycle - 1:
            wait_time = 0
            age = 0
        else:
            wait_time += 1
            age += 1

        base_score = _workflow_priority_score(workspace_id, workflow)
        aging_bonus = min(0.25, float(wait_time) * 0.05)
        starvation_bonus = 0.2 if wait_time >= int(runtime["starvation_threshold"]) else 0.0
        boost = round(aging_bonus + starvation_bonus, 3)
        starvation_score = round(_clamp01(float(wait_time) / float(runtime["starvation_threshold"])), 3)
        score = round(_clamp01(base_score + boost), 3)

        workflow["workflow_wait_time"] = wait_time
        workflow["workflow_age"] = age
        workflow["priority_boost"] = boost
        workflow["starvation_score"] = starvation_score
        workflow["priority"] = score
        workflow["updated_at"] = _utc_now()
        ranked.append((workflow_id, score))
        if wait_time >= int(runtime["fairness_window"]):
            fairness_candidates.append((workflow_id, wait_time))

    ranked.sort(key=lambda row: row[1], reverse=True)
    target_active = [workflow_id for workflow_id, _ in ranked[: runtime["available_slots"]]]
    previous_active = [str(wf_id) for wf_id in (runtime.get("active_workflows") or [])]
    prev_set = set(previous_active)
    target_set = set(target_active)

    runtime.update(
        {
            "active_workflows": target_active,
            "updated_at": _utc_now(),
        }
    )
    emitted: list[dict[str, Any]] = []

    for workflow_id, score in ranked:
        workflow = workflows.get(workflow_id)
        if workflow is None:
            continue
        wait_time = int(workflow.get("workflow_wait_time", 0))
        boost = float(workflow.get("priority_boost", 0.0))
        starvation_score = float(workflow.get("starvation_score", 0.0))

        if wait_time > 0:
            emitted.append(
                EVENTS.append(
                    "agent.supervisor_aged",
                    {
                        "workspace_id": workspace_id,
                        "workflow_id": workflow_id,
                        "workflow_age": int(workflow.get("workflow_age", 0)),
                        "workflow_wait_time": wait_time,
                    },
                    execution_id=execution_id,
                    source=source,
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
            )

        if boost > 0:
            emitted.append(
                EVENTS.append(
                    "agent.supervisor_boosted",
                    {
                        "workspace_id": workspace_id,
                        "workflow_id": workflow_id,
                        "base_priority": round(max(0.0, score - boost), 3),
                        "priority_boost": boost,
                        "effective_priority": score,
                        "workflow_wait_time": wait_time,
                    },
                    execution_id=execution_id,
                    source=source,
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
            )

        if wait_time >= int(runtime["starvation_threshold"]):
            emitted.append(
                EVENTS.append(
                    "agent.supervisor_starvation_detected",
                    {
                        "workspace_id": workspace_id,
                        "workflow_id": workflow_id,
                        "workflow_wait_time": wait_time,
                        "starvation_score": starvation_score,
                    },
                    execution_id=execution_id,
                    source=source,
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
            )

    if fairness_candidates and target_active:
        fairness_candidates.sort(key=lambda row: row[1], reverse=True)
        fairness_workflow = fairness_candidates[0][0]
        replaced = target_active[-1]
        applied = False
        if fairness_workflow not in target_set:
            target_active[-1] = fairness_workflow
            target_set = set(target_active)
            applied = True

        emitted.append(
            EVENTS.append(
                "agent.supervisor_fairness_applied",
                {
                    "workspace_id": workspace_id,
                    "reason": "fairness_window_enforced",
                    "fairness_window": int(runtime["fairness_window"]),
                    "starvation_threshold": int(runtime["starvation_threshold"]),
                    "selected_workflow": fairness_workflow,
                    "replaced_workflow": replaced,
                    "applied": applied,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

        if applied:
            runtime.update(
                {
                    "active_workflows": target_active,
                    "updated_at": _utc_now(),
                }
            )
    prioritized_event = EVENTS.append(
        "agent.supervisor_prioritized",
        {
            "workspace_id": workspace_id,
            "trigger": trigger,
            "reason": reason,
            "actor": actor,
            "available_slots": runtime["available_slots"],
            "fairness_window": int(runtime["fairness_window"]),
            "starvation_threshold": int(runtime["starvation_threshold"]),
            "ranking": [
                {
                    "workflow_id": workflow_id,
                    "priority": score,
                }
                for workflow_id, score in ranked
            ],
            "active_workflows": target_active,
        },
        execution_id=execution_id,
        source=source,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
    )
    emitted.append(prioritized_event)

    preempted = sorted(prev_set - target_set)
    activated = sorted(target_set - prev_set)
    for workflow_id in preempted:
        workflow = workflows.get(workflow_id)
        if workflow is not None:
            workflow["status"] = "preempted"
            workflow["updated_at"] = _utc_now()
        emitted.append(
            EVENTS.append(
                "agent.supervisor_preempted",
                {
                    "workspace_id": workspace_id,
                    "workflow_id": workflow_id,
                    "reason": "slot contention",
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for workflow_id in activated:
        workflow = workflows.get(workflow_id)
        if workflow is not None:
            workflow["status"] = "active"
            workflow["workflow_wait_time"] = 0
            workflow["workflow_age"] = 0
            workflow["scheduler_cycle_last_scheduled"] = cycle
            workflow["updated_at"] = _utc_now()
        emitted.append(
            EVENTS.append(
                "agent.supervisor_reallocated",
                {
                    "workspace_id": workspace_id,
                    "workflow_id": workflow_id,
                    "reason": "priority promotion",
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for from_workflow, to_workflow in zip(preempted, activated):
        emitted.append(
            EVENTS.append(
                "agent.supervisor_transferred",
                {
                    "workspace_id": workspace_id,
                    "from_workflow": from_workflow,
                    "to_workflow": to_workflow,
                    "reason": "priority rebalance",
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for workflow_id in target_active:
        workflow = workflows.get(workflow_id)
        if workflow is None:
            continue
        workflow["scheduler_cycle_last_scheduled"] = cycle
        workflow["workflow_wait_time"] = 0
        workflow["workflow_age"] = 0
        emitted.append(
            EVENTS.append(
                "agent.workflow_health",
                {
                    "health": _workflow_health_payload(workspace_id, workflow),
                    "workspace_id": workspace_id,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    return runtime, emitted


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


@app.get("/api/agents/workflows")
def list_agent_workflows(workspace_id: str = "andie-default") -> dict[str, Any]:
    workflows = _get_workspace_workflows(workspace_id)
    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "workflows": list(workflows.values()),
    }


@app.post("/api/agents/assign")
async def assign_agent_task(request: AgentAssignmentRequest) -> dict[str, Any]:
    try:
        role = _normalize_agent_role(request.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task = _create_assigned_task(
        task_id=request.task_id,
        role=role,
        objective_id=request.objective_id,
        payload=request.payload,
        actor=request.actor,
        reason=request.reason,
        workspace_id=request.workspace_id,
        strategy="operator_forced",
    )

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


@app.post("/api/agents/arbitrate")
async def arbitrate_agent_task(request: AgentArbitrationRequest) -> dict[str, Any]:
    if request.objective_id is not None and request.objective_id not in OBJECTIVES:
        raise HTTPException(status_code=404, detail=f"unknown objective_id: {request.objective_id}")

    try:
        strategy, role, strategy_inputs = _select_agent_strategy_and_role(
            workspace_id=request.workspace_id,
            objective_id=request.objective_id,
            operator_forced_role=request.operator_forced_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    decision_context_event = EVENTS.append(
        "agent.decision_context",
        {
            "task_id": request.task_id,
            "objective_id": request.objective_id,
            "pressure_score": strategy_inputs.get("pressure_score"),
            "trust_score": strategy_inputs.get("trust_score"),
            "governance_band": strategy_inputs.get("governance_band"),
            "workspace_profile": strategy_inputs.get("workspace_profile"),
            "selected_strategy": strategy,
            "selected_role": role,
            "constraints": strategy_inputs.get("constraints"),
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    await _fanout_event(decision_context_event)

    strategy_event = EVENTS.append(
        "agent.assignment_strategy",
        {
            "task_id": request.task_id,
            "objective_id": request.objective_id,
            "strategy": strategy,
            "selected_role": role,
            "inputs": strategy_inputs,
            "workspace_id": request.workspace_id,
            "actor": request.actor,
            "reason": request.reason,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    await _fanout_event(strategy_event)

    workflow, workflow_reason = _build_collaboration_plan(
        selected_role=role,
        strategy=strategy,
        strategy_inputs=strategy_inputs,
    )
    collaboration_plan_event = EVENTS.append(
        "agent.collaboration_plan",
        {
            "task_id": request.task_id,
            "objective_id": request.objective_id,
            "workflow": workflow,
            "reason": workflow_reason,
            "workspace_id": request.workspace_id,
            "selected_strategy": strategy,
            "selected_role": role,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    await _fanout_event(collaboration_plan_event)

    workflow_store = _get_workspace_workflows(request.workspace_id)
    workflow_state = _build_workflow(
        task_id=request.task_id,
        objective_id=request.objective_id,
        workflow_roles=workflow,
        reason=workflow_reason,
        selected_strategy=strategy,
        selected_role=role,
        workspace_id=request.workspace_id,
    )
    workflow_store[request.task_id] = workflow_state

    workflow_started_event = EVENTS.append(
        "agent.workflow_started",
        {
            "workflow": workflow_state,
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    await _fanout_event(workflow_started_event)

    workflow_health_event = EVENTS.append(
        "agent.workflow_health",
        {
            "health": _workflow_health_payload(request.workspace_id, workflow_state),
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    await _fanout_event(workflow_health_event)

    task = _create_assigned_task(
        task_id=request.task_id,
        role=role,
        objective_id=request.objective_id,
        payload=request.payload,
        actor=request.actor,
        reason=request.reason,
        workspace_id=request.workspace_id,
        strategy=strategy,
    )

    assigned_event = EVENTS.append(
        "agent.assigned",
        {
            "task": task,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    await _fanout_event(assigned_event)

    return {
        "status": "ok",
        "strategy": strategy,
        "role": role,
        "collaboration_plan": {
            "workflow": workflow,
            "reason": workflow_reason,
        },
        "workflow": workflow_state,
        "task": task,
        "emitted_events": [
            decision_context_event,
            strategy_event,
            collaboration_plan_event,
            workflow_started_event,
            workflow_health_event,
            assigned_event,
        ],
    }


@app.post("/api/agents/workflows/{workflow_id}/update")
async def update_agent_workflow(workflow_id: str, request: AgentWorkflowUpdateRequest) -> dict[str, Any]:
    workflows = _get_workspace_workflows(request.workspace_id)
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail=f"unknown workflow_id: {workflow_id}")

    workflow = workflows[workflow_id]
    status_value = request.status.strip().lower()
    now = _utc_now()
    emitted: list[dict[str, Any]] = []

    workflow["updated_at"] = now
    workflow["history"].append(
        {
            "at": now,
            "status": status_value,
            "step_role": request.step_role,
            "reason": request.reason,
            "payload": request.payload,
        }
    )

    if status_value == "blocked":
        workflow["status"] = "blocked"
        workflow["blocked_steps"] = int(workflow.get("blocked_steps", 0)) + 1
        workflow["workflow_pressure_score"] = _workflow_pressure_score(
            workspace_id=request.workspace_id,
            objective_id=workflow.get("objective_id"),
            blocked_steps=int(workflow.get("blocked_steps", 0)),
        )

        blocked_event = EVENTS.append(
            "agent.workflow_blocked",
            {
                "workflow": workflow,
                "workspace_id": request.workspace_id,
                "reason": request.reason,
            },
            execution_id=request.execution_id,
            source=request.source,
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
        )
        emitted.append(blocked_event)

        profile = str(_get_governance_profile_binding(request.workspace_id).get("active", "balanced"))
        replanned_roles, replanned_reason = _replan_workflow_roles(workflow, profile)
        workflow["workflow"] = replanned_roles
        workflow["status"] = "replanned"
        workflow["current_step_index"] = 0
        workflow["reason"] = replanned_reason
        workflow["replan_count"] = int(workflow.get("replan_count", 0)) + 1

        replanned_event = EVENTS.append(
            "agent.workflow_replanned",
            {
                "workflow": workflow,
                "workspace_id": request.workspace_id,
                "reason": replanned_reason,
            },
            execution_id=request.execution_id,
            source=request.source,
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
        )
        emitted.append(replanned_event)

        supervisor_invoked = EVENTS.append(
            "agent.supervisor_invoked",
            {
                "workflow_id": workflow_id,
                "trigger": "workflow_blocked",
                "workspace_id": request.workspace_id,
                "reason": request.reason,
            },
            execution_id=request.execution_id,
            source="workflow-supervisor",
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
        )
        emitted.append(supervisor_invoked)

        supervisor_event_type, supervisor_reason = _supervisor_apply(
            workflow=workflow,
            workspace_id=request.workspace_id,
            trigger="workflow_blocked",
        )
        workflow["supervisor_actions"] = int(workflow.get("supervisor_actions", 0)) + 1
        workflow["history"].append(
            {
                "at": _utc_now(),
                "status": supervisor_event_type,
                "reason": supervisor_reason,
                "trigger": "workflow_blocked",
            }
        )

        supervisor_event = EVENTS.append(
            supervisor_event_type,
            {
                "workflow": workflow,
                "workspace_id": request.workspace_id,
                "reason": supervisor_reason,
            },
            execution_id=request.execution_id,
            source="workflow-supervisor",
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
        )
        emitted.append(supervisor_event)

        _, arbitration_events = _run_supervisor_arbitration(
            workspace_id=request.workspace_id,
            available_slots=int(_get_supervisor_runtime(request.workspace_id).get("available_slots", 1)),
            fairness_window=int(_get_supervisor_runtime(request.workspace_id).get("fairness_window", 3)),
            starvation_threshold=int(_get_supervisor_runtime(request.workspace_id).get("starvation_threshold", 3)),
            execution_id=request.execution_id,
            source="workflow-supervisor",
            correlation_id=request.correlation_id,
            trigger="workflow_blocked",
            reason=request.reason,
            actor="workflow-supervisor",
        )
        emitted.extend(arbitration_events)

    elif status_value in {"updated", "in_progress"}:
        workflow["status"] = "in_progress"
        workflow["workflow_pressure_score"] = _workflow_pressure_score(
            workspace_id=request.workspace_id,
            objective_id=workflow.get("objective_id"),
            blocked_steps=int(workflow.get("blocked_steps", 0)),
        )

    elif status_value == "completed":
        workflow["status"] = "completed"
        workflow["current_step_index"] = len(workflow.get("workflow", []))
        workflow["workflow_pressure_score"] = _workflow_pressure_score(
            workspace_id=request.workspace_id,
            objective_id=workflow.get("objective_id"),
            blocked_steps=int(workflow.get("blocked_steps", 0)),
        )
        completed_event = EVENTS.append(
            "agent.workflow_completed",
            {
                "workflow": workflow,
                "workspace_id": request.workspace_id,
            },
            execution_id=request.execution_id,
            source=request.source,
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
        )
        emitted.append(completed_event)
    else:
        raise HTTPException(status_code=400, detail=f"unsupported workflow status: {request.status}")

    updated_event = EVENTS.append(
        "agent.workflow_updated",
        {
            "workflow": workflow,
            "workspace_id": request.workspace_id,
            "status": workflow.get("status"),
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    emitted.insert(0, updated_event)

    health_event = EVENTS.append(
        "agent.workflow_health",
        {
            "health": _workflow_health_payload(request.workspace_id, workflow),
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )
    emitted.insert(1, health_event)

    for event in emitted:
        await _fanout_event(event)

    return {
        "status": "ok",
        "workflow": workflow,
        "emitted_events": emitted,
    }


@app.post("/api/agents/workflows/{workflow_id}/delegate")
async def delegate_agent_workflow(workflow_id: str, request: AgentWorkflowDelegationRequest) -> dict[str, Any]:
    workflows = _get_workspace_workflows(request.workspace_id)
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail=f"unknown workflow_id: {workflow_id}")

    try:
        from_role = _normalize_agent_role(request.from_role)
        to_role = _normalize_agent_role(request.to_role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workflow = workflows[workflow_id]
    workflow["history"].append(
        {
            "at": _utc_now(),
            "status": "delegated",
            "from_role": from_role,
            "to_role": to_role,
            "reason": request.reason,
            "payload": request.payload,
        }
    )
    workflow["updated_at"] = _utc_now()
    if to_role not in [str(role) for role in workflow.get("workflow", [])]:
        workflow["workflow"] = [to_role] + [str(role) for role in workflow.get("workflow", [])]

    delegated_event = EVENTS.append(
        "agent.delegated",
        {
            "workflow_id": workflow_id,
            "from_role": from_role,
            "to_role": to_role,
            "reason": request.reason,
            "payload": request.payload,
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    health_event = EVENTS.append(
        "agent.workflow_health",
        {
            "health": _workflow_health_payload(request.workspace_id, workflow),
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    await _fanout_event(delegated_event)
    await _fanout_event(health_event)

    return {
        "status": "ok",
        "workflow": workflow,
        "emitted_events": [delegated_event, health_event],
    }


@app.post("/api/agents/workflows/{workflow_id}/review")
async def review_agent_workflow(workflow_id: str, request: AgentWorkflowReviewRequest) -> dict[str, Any]:
    workflows = _get_workspace_workflows(request.workspace_id)
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail=f"unknown workflow_id: {workflow_id}")

    try:
        reviewer_role = _normalize_agent_role(request.reviewer_role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workflow = workflows[workflow_id]
    status_value = request.status.strip().lower()
    if status_value not in {"requested", "completed"}:
        raise HTTPException(status_code=400, detail=f"unsupported review status: {request.status}")

    workflow["updated_at"] = _utc_now()
    workflow["history"].append(
        {
            "at": _utc_now(),
            "status": f"review_{status_value}",
            "reviewer_role": reviewer_role,
            "reason": request.reason,
            "payload": request.payload,
        }
    )

    event_type = "agent.review_requested" if status_value == "requested" else "agent.review_completed"
    review_event = EVENTS.append(
        event_type,
        {
            "workflow_id": workflow_id,
            "reviewer_role": reviewer_role,
            "reason": request.reason,
            "payload": request.payload,
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    await _fanout_event(review_event)
    return {
        "status": "ok",
        "workflow": workflow,
        "event": review_event,
    }


@app.post("/api/agents/workflows/{workflow_id}/consensus")
async def consensus_agent_workflow(workflow_id: str, request: AgentWorkflowConsensusRequest) -> dict[str, Any]:
    workflows = _get_workspace_workflows(request.workspace_id)
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail=f"unknown workflow_id: {workflow_id}")

    participants: list[str] = []
    try:
        participants = [_normalize_agent_role(role) for role in request.participants]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workflow = workflows[workflow_id]
    workflow["updated_at"] = _utc_now()
    workflow["history"].append(
        {
            "at": _utc_now(),
            "status": "consensus_started",
            "participants": participants,
            "reason": request.reason,
            "payload": request.payload,
        }
    )

    started_event = EVENTS.append(
        "agent.consensus_started",
        {
            "workflow_id": workflow_id,
            "participants": participants,
            "reason": request.reason,
            "payload": request.payload,
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    outcome_type = "agent.consensus_reached" if request.reached else "agent.consensus_failed"
    workflow["history"].append(
        {
            "at": _utc_now(),
            "status": "consensus_reached" if request.reached else "consensus_failed",
            "participants": participants,
            "resolution": request.resolution,
            "reason": request.reason,
        }
    )
    outcome_event = EVENTS.append(
        outcome_type,
        {
            "workflow_id": workflow_id,
            "participants": participants,
            "resolution": request.resolution,
            "reason": request.reason,
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    supervisor_events: list[dict[str, Any]] = []
    if not request.reached:
        supervisor_invoked = EVENTS.append(
            "agent.supervisor_invoked",
            {
                "workflow_id": workflow_id,
                "trigger": "consensus_failed",
                "workspace_id": request.workspace_id,
                "reason": request.reason,
            },
            execution_id=request.execution_id,
            source="workflow-supervisor",
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
        )
        supervisor_events.append(supervisor_invoked)

        supervisor_event_type, supervisor_reason = _supervisor_apply(
            workflow=workflow,
            workspace_id=request.workspace_id,
            trigger="consensus_failed",
        )
        workflow["supervisor_actions"] = int(workflow.get("supervisor_actions", 0)) + 1
        workflow["history"].append(
            {
                "at": _utc_now(),
                "status": supervisor_event_type,
                "reason": supervisor_reason,
                "trigger": "consensus_failed",
            }
        )

        supervisor_event = EVENTS.append(
            supervisor_event_type,
            {
                "workflow": workflow,
                "workspace_id": request.workspace_id,
                "reason": supervisor_reason,
            },
            execution_id=request.execution_id,
            source="workflow-supervisor",
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
        )
        supervisor_events.append(supervisor_event)

        _, arbitration_events = _run_supervisor_arbitration(
            workspace_id=request.workspace_id,
            available_slots=int(_get_supervisor_runtime(request.workspace_id).get("available_slots", 1)),
            fairness_window=int(_get_supervisor_runtime(request.workspace_id).get("fairness_window", 3)),
            starvation_threshold=int(_get_supervisor_runtime(request.workspace_id).get("starvation_threshold", 3)),
            execution_id=request.execution_id,
            source="workflow-supervisor",
            correlation_id=request.correlation_id,
            trigger="consensus_failed",
            reason=request.reason,
            actor="workflow-supervisor",
        )
        supervisor_events.extend(arbitration_events)

    health_event = EVENTS.append(
        "agent.workflow_health",
        {
            "health": _workflow_health_payload(request.workspace_id, workflow),
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    await _fanout_event(started_event)
    await _fanout_event(outcome_event)
    for event in supervisor_events:
        await _fanout_event(event)
    await _fanout_event(health_event)

    return {
        "status": "ok",
        "workflow": workflow,
        "emitted_events": [started_event, outcome_event] + supervisor_events + [health_event],
    }


@app.post("/api/agents/workflows/{workflow_id}/supervise")
async def supervise_agent_workflow(workflow_id: str, request: AgentWorkflowSupervisionRequest) -> dict[str, Any]:
    workflows = _get_workspace_workflows(request.workspace_id)
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail=f"unknown workflow_id: {workflow_id}")

    workflow = workflows[workflow_id]
    workflow["updated_at"] = _utc_now()

    invoked_event = EVENTS.append(
        "agent.supervisor_invoked",
        {
            "workflow_id": workflow_id,
            "trigger": request.trigger,
            "workspace_id": request.workspace_id,
            "reason": request.reason,
            "payload": request.payload,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    supervisor_event_type, supervisor_reason = _supervisor_apply(
        workflow=workflow,
        workspace_id=request.workspace_id,
        trigger=request.trigger,
    )
    workflow["supervisor_actions"] = int(workflow.get("supervisor_actions", 0)) + 1
    workflow["history"].append(
        {
            "at": _utc_now(),
            "status": supervisor_event_type,
            "trigger": request.trigger,
            "reason": supervisor_reason,
            "payload": request.payload,
        }
    )

    supervisor_event = EVENTS.append(
        supervisor_event_type,
        {
            "workflow": workflow,
            "workspace_id": request.workspace_id,
            "reason": supervisor_reason,
            "trigger": request.trigger,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    health_event = EVENTS.append(
        "agent.workflow_health",
        {
            "health": _workflow_health_payload(request.workspace_id, workflow),
            "workspace_id": request.workspace_id,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    _, arbitration_events = _run_supervisor_arbitration(
        workspace_id=request.workspace_id,
        available_slots=int(_get_supervisor_runtime(request.workspace_id).get("available_slots", 1)),
        fairness_window=int(_get_supervisor_runtime(request.workspace_id).get("fairness_window", 3)),
        starvation_threshold=int(_get_supervisor_runtime(request.workspace_id).get("starvation_threshold", 3)),
        execution_id=request.execution_id,
        source=request.source,
        correlation_id=request.correlation_id,
        trigger=request.trigger,
        reason=request.reason,
        actor=request.actor,
    )

    for event in [invoked_event, supervisor_event, health_event] + arbitration_events:
        await _fanout_event(event)

    return {
        "status": "ok",
        "workflow": workflow,
        "emitted_events": [invoked_event, supervisor_event, health_event] + arbitration_events,
    }


@app.post("/api/agents/supervisor/arbitrate")
async def arbitrate_supervisor_runtime(request: AgentSupervisorArbitrationRequest) -> dict[str, Any]:
    runtime, emitted_events = _run_supervisor_arbitration(
        workspace_id=request.workspace_id,
        available_slots=request.available_slots,
        fairness_window=request.fairness_window,
        starvation_threshold=request.starvation_threshold,
        execution_id=request.execution_id,
        source=request.source,
        correlation_id=request.correlation_id,
        trigger=request.trigger,
        reason=request.reason,
        actor=request.actor,
    )

    for event in emitted_events:
        await _fanout_event(event)

    return {
        "status": "ok",
        "workspace_id": request.workspace_id,
        "runtime": runtime,
        "emitted_events": emitted_events,
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
