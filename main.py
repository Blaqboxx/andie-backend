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
        "agent.scheduler_policy_applied",
        "agent.scheduler_policy_changed",
        "agent.scheduler_policy_escalated",
        "agent.scheduler_policy_relaxed",
        "agent.scheduler_policy_recommended",
        "agent.scheduler_confidence",
        "agent.scheduler_effectiveness_scored",
        "agent.scheduler_decay_applied",
        "agent.scheduler_contention_smoothed",
        "agent.assigned",
        "agent.completed",
        "agent.blocked",
        "agent.escalated",
    },
    "coordinator": {
        "coordinator.recommendation_created",
        "coordinator.priority_ranked",
        "coordinator.blocked_objective_detected",
        "coordinator.merge_candidate_detected",
        "coordinator.suspension_recommended",
        "coordinator.escalation_recommended",
        "coordinator.portfolio_created",
        "coordinator.portfolio_ranked",
        "coordinator.portfolio_blocked",
        "coordinator.portfolio_risk_detected",
        "coordinator.portfolio_health_updated",
        "coordinator.portfolio_priority_changed",
        "coordinator.portfolio_dependency_detected",
        "coordinator.portfolio_resource_conflict_detected",
        "coordinator.portfolio_escalation_recommended",
        "coordinator.portfolio_suspension_recommended",
        "coordinator.portfolio_governance_review_required",
        "coordinator.portfolio_recommendation_suppressed",
        "coordinator.portfolio_policy_applied",
        "coordinator.portfolio_policy_conflict_detected",
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

SCHEDULER_POLICY_BY_WORKSPACE: dict[str, dict[str, Any]] = {
    "andie-default": {
        "scheduler_profile": "balanced",
        "fairness_curve": "linear",
        "starvation_recovery": "normal",
        "preemption_policy": "allowed",
        "fairness_window": 3,
        "starvation_threshold": 3,
        "adaptive_mode": False,
        "optimization": {
            "last_escalation_cycle": 0,
            "last_relaxation_cycle": 0,
            "effectiveness_score": 0.0,
            "optimization_history": [],
            "decay_cycles": 5,
            "confidence": 0.0,
            "last_average_pressure": 0.0,
        },
        "updated_at": _utc_now(),
    }
}

COORDINATOR_STATE_BY_WORKSPACE: dict[str, dict[str, Any]] = {
    "andie-default": {
        "active_objectives": [],
        "blocked_objectives": [],
        "objective_dependencies": [],
        "workflow_assignments": [],
        "priority_ranking": [],
        "coordination_recommendations": [],
        "merge_candidates": [],
        "objective_portfolios": [],
        "portfolio_ranking": [],
        "portfolio_health": [],
        "cross_portfolio_dependencies": [],
        "portfolio_resource_conflicts": [],
        "portfolio_policy": {},
        "portfolio_policy_conflicts": [],
        "portfolio_suppressed_recommendations": [],
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
    portfolio_group: str | None = None
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


class AgentSchedulerPolicyApplyRequest(BaseModel):
    scheduler_profile: str = "balanced"
    fairness_curve: str | None = None
    starvation_recovery: str | None = None
    preemption_policy: str | None = None
    fairness_window: int | None = None
    starvation_threshold: int | None = None
    optimization_decay_cycles: int | None = None
    adaptive_mode: bool | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    actor: str = "operator"
    reason: str = "scheduler policy selection"
    execution_id: str | None = None
    source: str = "scheduler-policy"
    workspace_id: str = "andie-default"
    correlation_id: str | None = None


class CoordinatorAnalyzeRequest(BaseModel):
    reason: str = "coordinator analysis"
    actor: str = "runtime-coordinator"
    execution_id: str | None = None
    source: str = "runtime-coordinator"
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


def _scheduler_policy_defaults(profile: str) -> dict[str, Any]:
    profile_value = profile.strip().lower()
    profiles: dict[str, dict[str, Any]] = {
        "throughput": {
            "scheduler_profile": "throughput",
            "fairness_curve": "linear",
            "starvation_recovery": "soft",
            "preemption_policy": "never",
            "fairness_window": 6,
            "starvation_threshold": 6,
            "adaptive_mode": False,
            "optimization": {
                "last_escalation_cycle": 0,
                "last_relaxation_cycle": 0,
                "effectiveness_score": 0.0,
                "optimization_history": [],
                "decay_cycles": 5,
                "confidence": 0.0,
                "last_average_pressure": 0.0,
            },
        },
        "balanced": {
            "scheduler_profile": "balanced",
            "fairness_curve": "linear",
            "starvation_recovery": "normal",
            "preemption_policy": "allowed",
            "fairness_window": 3,
            "starvation_threshold": 3,
            "adaptive_mode": False,
            "optimization": {
                "last_escalation_cycle": 0,
                "last_relaxation_cycle": 0,
                "effectiveness_score": 0.0,
                "optimization_history": [],
                "decay_cycles": 5,
                "confidence": 0.0,
                "last_average_pressure": 0.0,
            },
        },
        "fair": {
            "scheduler_profile": "fair",
            "fairness_curve": "weighted",
            "starvation_recovery": "normal",
            "preemption_policy": "allowed",
            "fairness_window": 2,
            "starvation_threshold": 2,
            "adaptive_mode": False,
            "optimization": {
                "last_escalation_cycle": 0,
                "last_relaxation_cycle": 0,
                "effectiveness_score": 0.0,
                "optimization_history": [],
                "decay_cycles": 5,
                "confidence": 0.0,
                "last_average_pressure": 0.0,
            },
        },
        "mission_critical": {
            "scheduler_profile": "mission_critical",
            "fairness_curve": "exponential",
            "starvation_recovery": "aggressive",
            "preemption_policy": "aggressive",
            "fairness_window": 4,
            "starvation_threshold": 2,
            "adaptive_mode": True,
            "optimization": {
                "last_escalation_cycle": 0,
                "last_relaxation_cycle": 0,
                "effectiveness_score": 0.0,
                "optimization_history": [],
                "decay_cycles": 5,
                "confidence": 0.0,
                "last_average_pressure": 0.0,
            },
        },
    }
    if profile_value not in profiles:
        raise ValueError(f"unknown scheduler profile: {profile}")
    return dict(profiles[profile_value])


def _get_scheduler_policy(workspace_id: str) -> dict[str, Any]:
    if workspace_id not in SCHEDULER_POLICY_BY_WORKSPACE:
        SCHEDULER_POLICY_BY_WORKSPACE[workspace_id] = {
            **_scheduler_policy_defaults("balanced"),
            "updated_at": _utc_now(),
        }
    policy = SCHEDULER_POLICY_BY_WORKSPACE[workspace_id]
    optimization = policy.get("optimization") or {}
    if not isinstance(optimization, dict):
        optimization = {}
    optimization.setdefault("last_escalation_cycle", 0)
    optimization.setdefault("last_relaxation_cycle", 0)
    optimization.setdefault("effectiveness_score", 0.0)
    optimization.setdefault("optimization_history", [])
    optimization.setdefault("decay_cycles", 5)
    optimization.setdefault("confidence", 0.0)
    optimization.setdefault("last_average_pressure", 0.0)
    history = optimization.get("optimization_history")
    optimization["optimization_history"] = history[-25:] if isinstance(history, list) else []
    policy["optimization"] = optimization
    return policy


def _apply_scheduler_policy(request: AgentSchedulerPolicyApplyRequest) -> dict[str, Any]:
    defaults = _scheduler_policy_defaults(request.scheduler_profile)
    policy = _get_scheduler_policy(request.workspace_id)

    if request.fairness_curve is not None:
        curve = request.fairness_curve.strip().lower()
        if curve not in {"linear", "weighted", "exponential"}:
            raise ValueError(f"unknown fairness curve: {request.fairness_curve}")
        defaults["fairness_curve"] = curve

    if request.starvation_recovery is not None:
        recovery = request.starvation_recovery.strip().lower()
        if recovery not in {"soft", "normal", "aggressive"}:
            raise ValueError(f"unknown starvation recovery: {request.starvation_recovery}")
        defaults["starvation_recovery"] = recovery

    if request.preemption_policy is not None:
        preemption = request.preemption_policy.strip().lower()
        if preemption not in {"never", "allowed", "aggressive"}:
            raise ValueError(f"unknown preemption policy: {request.preemption_policy}")
        defaults["preemption_policy"] = preemption

    if request.fairness_window is not None:
        defaults["fairness_window"] = max(1, int(request.fairness_window))

    if request.starvation_threshold is not None:
        defaults["starvation_threshold"] = max(1, int(request.starvation_threshold))

    optimization = defaults.get("optimization") if isinstance(defaults.get("optimization"), dict) else {}
    if request.optimization_decay_cycles is not None:
        optimization["decay_cycles"] = max(1, int(request.optimization_decay_cycles))
    defaults["optimization"] = optimization

    if request.adaptive_mode is not None:
        defaults["adaptive_mode"] = bool(request.adaptive_mode)

    defaults["overrides"] = dict(request.overrides)
    defaults["updated_at"] = _utc_now()
    policy.clear()
    policy.update(defaults)
    _ = _get_scheduler_policy(request.workspace_id)
    return policy


def _get_coordinator_state(workspace_id: str) -> dict[str, Any]:
    if workspace_id not in COORDINATOR_STATE_BY_WORKSPACE:
        COORDINATOR_STATE_BY_WORKSPACE[workspace_id] = {
            "active_objectives": [],
            "blocked_objectives": [],
            "objective_dependencies": [],
            "workflow_assignments": [],
            "priority_ranking": [],
            "coordination_recommendations": [],
            "merge_candidates": [],
            "objective_portfolios": [],
            "portfolio_ranking": [],
            "portfolio_health": [],
            "cross_portfolio_dependencies": [],
            "portfolio_resource_conflicts": [],
            "portfolio_policy": {},
            "portfolio_policy_conflicts": [],
            "portfolio_suppressed_recommendations": [],
            "updated_at": _utc_now(),
        }
    return COORDINATOR_STATE_BY_WORKSPACE[workspace_id]


def _portfolio_policy_overlay(profile: str, governance_band: str) -> dict[str, Any]:
    profile_value = str(profile or "balanced").strip().lower()
    defaults: dict[str, dict[str, Any]] = {
        "balanced": {
            "escalation_threshold": 0.7,
            "suspension_threshold": 0.65,
            "max_pressure_for_suspension": 0.4,
            "require_governance_review_for_escalation": False,
            "suppress_suspend_when_escalated": True,
            "conflict_resolution": "prefer_escalation",
        },
        "conservative": {
            "escalation_threshold": 0.8,
            "suspension_threshold": 0.6,
            "max_pressure_for_suspension": 0.45,
            "require_governance_review_for_escalation": True,
            "suppress_suspend_when_escalated": True,
            "conflict_resolution": "prefer_escalation",
        },
        "aggressive": {
            "escalation_threshold": 0.55,
            "suspension_threshold": 0.8,
            "max_pressure_for_suspension": 0.3,
            "require_governance_review_for_escalation": False,
            "suppress_suspend_when_escalated": True,
            "conflict_resolution": "prefer_escalation",
        },
        "mission_critical": {
            "escalation_threshold": 0.6,
            "suspension_threshold": 0.9,
            "max_pressure_for_suspension": 0.25,
            "require_governance_review_for_escalation": False,
            "suppress_suspend_when_escalated": True,
            "conflict_resolution": "prefer_escalation",
        },
    }
    policy = dict(defaults.get(profile_value, defaults["balanced"]))
    policy["active_profile"] = profile_value
    policy["governance_band"] = governance_band
    return policy


def _run_coordinator_analysis(
    *,
    workspace_id: str,
    reason: str,
    actor: str,
    execution_id: str | None,
    source: str,
    correlation_id: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    previous_state = _get_coordinator_state(workspace_id)
    previous_portfolio_ranking = [
        str(item.get("portfolio_id"))
        for item in (previous_state.get("portfolio_ranking") or [])
        if str(item.get("portfolio_id") or "")
    ]

    signals = _derive_objective_signals()
    governance_state = _get_governance_state(workspace_id)
    trust_state = _get_trust_state(workspace_id)
    scheduler_policy = _get_scheduler_policy(workspace_id)
    workflows = _get_workspace_workflows(workspace_id)

    active_objectives: list[dict[str, Any]] = []
    blocked_objectives: list[dict[str, Any]] = []
    objective_dependencies: list[dict[str, Any]] = []
    pressure_scores = signals.get("objective_pressure_score") or {}
    blocked_map = signals.get("blocked") or {}
    critical_path = signals.get("critical_path") or {}

    for objective_id, objective in OBJECTIVES.items():
        if not _is_objective_active(objective):
            continue

        record = {
            "objective_id": objective_id,
            "title": str(objective.get("title", "")),
            "pressure_score": float(pressure_scores.get(objective_id, 0.0)),
            "critical_path": int(critical_path.get(objective_id, 0)),
            "blocked": bool(blocked_map.get(objective_id, False)),
            "priority": int(objective.get("priority", 0)),
            "salience": float(objective.get("salience", 0.0)),
            "portfolio_group": objective.get("portfolio_group"),
        }
        active_objectives.append(record)

        if record["blocked"]:
            blocked_objectives.append(
                {
                    **record,
                    "depends_on": [str(dep) for dep in objective.get("depends_on", [])],
                    "blocked_by": [str(dep) for dep in objective.get("blocked_by", [])],
                }
            )

        for dep in objective.get("depends_on", []):
            objective_dependencies.append(
                {
                    "objective_id": objective_id,
                    "depends_on": str(dep),
                    "relation": "depends_on",
                }
            )

    active_objectives.sort(key=lambda item: (item["pressure_score"], item["critical_path"], item["priority"]), reverse=True)

    priority_ranking = [
        {
            "rank": index + 1,
            "objective_id": item["objective_id"],
            "pressure_score": item["pressure_score"],
            "blocked": item["blocked"],
            "critical_path": item["critical_path"],
        }
        for index, item in enumerate(active_objectives)
    ]

    workflow_assignments = [
        {
            "workflow_id": workflow_id,
            "objective_id": workflow.get("objective_id"),
            "status": workflow.get("status"),
            "workflow": [str(role) for role in workflow.get("workflow", [])],
            "selected_role": workflow.get("selected_role"),
            "workflow_pressure_score": float(workflow.get("workflow_pressure_score", 0.0)),
            "starvation_score": float(workflow.get("starvation_score", 0.0)),
            "replan_count": int(workflow.get("replan_count", 0)),
            "supervisor_actions": int(workflow.get("supervisor_actions", 0)),
        }
        for workflow_id, workflow in workflows.items()
        if str(workflow.get("status", "")).lower() != "completed"
    ]

    grouped: dict[tuple[str, tuple[str, ...], str], list[str]] = defaultdict(list)
    for item in workflow_assignments:
        key = (
            str(item.get("objective_id") or ""),
            tuple(item.get("workflow") or []),
            str(item.get("selected_role") or ""),
        )
        grouped[key].append(str(item.get("workflow_id")))

    merge_candidates: list[dict[str, Any]] = []
    for (objective_id, workflow_path, selected_role), workflow_ids in grouped.items():
        if len(workflow_ids) < 2:
            continue
        merge_candidates.append(
            {
                "objective_id": objective_id,
                "workflow_ids": sorted(workflow_ids),
                "shared_workflow": list(workflow_path),
                "selected_role": selected_role,
                "reason": "same objective cluster and execution path",
            }
        )

    governance_band = str(governance_state.get("band", "stable"))
    governance_profile = str(_get_governance_profile_binding(workspace_id).get("active", "balanced"))
    portfolio_policy = _portfolio_policy_overlay(governance_profile, governance_band)
    coordination_recommendations: list[dict[str, Any]] = []
    policy_conflicts: list[dict[str, Any]] = []
    suppressed_recommendations: list[dict[str, Any]] = []
    governance_review_required: list[dict[str, Any]] = []
    reverse_dependencies: dict[str, list[str]] = defaultdict(list)
    for edge in objective_dependencies:
        reverse_dependencies[str(edge["depends_on"])].append(str(edge["objective_id"]))

    for blocked in blocked_objectives:
        blockers = [str(x) for x in blocked.get("depends_on", []) + blocked.get("blocked_by", [])]
        impacted = sorted({dependent for blocker in blockers for dependent in reverse_dependencies.get(blocker, [])})
        recommendation_action = "governance_review" if governance_band == "escalated" else "escalate_dependency"
        coordination_recommendations.append(
            {
                "type": "escalation_recommended",
                "action": recommendation_action,
                "objective_id": blocked["objective_id"],
                "blocked_by": blockers,
                "impacted_objectives": impacted,
                "reason": "blocked objective in active dependency chain",
            }
        )

    for candidate in merge_candidates:
        coordination_recommendations.append(
            {
                "type": "merge_candidate_detected",
                "action": "merge_workflows",
                "objective_id": candidate["objective_id"],
                "workflow_ids": candidate["workflow_ids"],
                "reason": candidate["reason"],
            }
        )

    if governance_band == "escalated" and priority_ranking:
        coordination_recommendations.append(
            {
                "type": "escalation_recommended",
                "action": "governance_review",
                "objective_id": priority_ranking[0]["objective_id"],
                "reason": "governance band escalated",
            }
        )

    for item in workflow_assignments:
        if item["starvation_score"] >= 0.8 and item["workflow_pressure_score"] < 0.4:
            coordination_recommendations.append(
                {
                    "type": "suspension_recommended",
                    "action": "suspend_low_priority_workflow",
                    "workflow_id": item["workflow_id"],
                    "reason": "high starvation with low pressure",
                }
            )

    # Build portfolio clusters over active objectives using dependency links.
    active_ids = {str(item["objective_id"]) for item in active_objectives}
    adjacency: dict[str, set[str]] = {objective_id: set() for objective_id in active_ids}
    for objective_id, objective in OBJECTIVES.items():
        if objective_id not in active_ids:
            continue
        for ref in objective.get("depends_on", []) + objective.get("blocked_by", []) + objective.get("enables", []):
            ref_id = str(ref)
            if ref_id not in active_ids:
                continue
            adjacency[objective_id].add(ref_id)
            adjacency.setdefault(ref_id, set()).add(objective_id)

    visited: set[str] = set()
    objective_portfolios: list[dict[str, Any]] = []
    workflow_by_objective: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for workflow in workflow_assignments:
        objective_id = str(workflow.get("objective_id") or "")
        if objective_id:
            workflow_by_objective[objective_id].append(workflow)

    for seed in sorted(active_ids):
        if seed in visited:
            continue

        seed_group = OBJECTIVES.get(seed, {}).get("portfolio_group")

        stack = [seed]
        component: list[str] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue

            current_group = OBJECTIVES.get(current, {}).get("portfolio_group")
            if current_group != seed_group:
                continue

            visited.add(current)
            component.append(current)
            for nxt in adjacency.get(current, set()):
                if nxt not in visited:
                    stack.append(nxt)

        objective_ids = sorted(component)
        blocked_ids = [objective_id for objective_id in objective_ids if bool(blocked_map.get(objective_id, False))]
        pressures = [float(pressure_scores.get(objective_id, 0.0)) for objective_id in objective_ids]
        workflow_ids = sorted(
            {
                str(workflow["workflow_id"])
                for objective_id in objective_ids
                for workflow in workflow_by_objective.get(objective_id, [])
            }
        )
        blocked_ratio = (len(blocked_ids) / float(len(objective_ids))) if objective_ids else 0.0
        avg_pressure = (sum(pressures) / float(len(pressures))) if pressures else 0.0
        governance_bias = 0.15 if governance_band == "escalated" else (0.08 if governance_band == "warning" else 0.0)
        portfolio_risk = round(_clamp01((0.55 * blocked_ratio) + (0.35 * avg_pressure) + governance_bias), 3)
        portfolio_health = round(_clamp01(1.0 - portfolio_risk), 3)
        portfolio_pressure = round(avg_pressure, 3)

        portfolio_id = f"portfolio-{objective_ids[0]}"
        if seed_group:
            portfolio_id = f"portfolio-{seed_group}"
        objective_portfolios.append(
            {
                "portfolio_id": portfolio_id,
                "portfolio_group": seed_group,
                "objective_ids": objective_ids,
                "blocked_objectives": blocked_ids,
                "workflow_ids": workflow_ids,
                "portfolio_pressure": portfolio_pressure,
                "portfolio_risk": portfolio_risk,
                "portfolio_health": portfolio_health,
                "resource_load": len(workflow_ids),
                "governance_band": governance_band,
            }
        )

    objective_portfolios.sort(
        key=lambda item: (item["portfolio_pressure"], item["portfolio_risk"], item["resource_load"]),
        reverse=True,
    )
    portfolio_ranking = [
        {
            "rank": index + 1,
            "portfolio_id": portfolio["portfolio_id"],
            "portfolio_pressure": portfolio["portfolio_pressure"],
            "portfolio_risk": portfolio["portfolio_risk"],
            "portfolio_health": portfolio["portfolio_health"],
            "resource_load": portfolio["resource_load"],
        }
        for index, portfolio in enumerate(objective_portfolios)
    ]
    portfolio_health = [
        {
            "portfolio_id": portfolio["portfolio_id"],
            "portfolio_health": portfolio["portfolio_health"],
            "portfolio_risk": portfolio["portfolio_risk"],
            "blocked_count": len(portfolio["blocked_objectives"]),
        }
        for portfolio in objective_portfolios
    ]

    objective_to_portfolio: dict[str, str] = {}
    for portfolio in objective_portfolios:
        portfolio_id = str(portfolio["portfolio_id"])
        for objective_id in portfolio["objective_ids"]:
            objective_to_portfolio[str(objective_id)] = portfolio_id

    dependency_index: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in objective_dependencies:
        dependent_objective = str(edge.get("objective_id") or "")
        blocker_objective = str(edge.get("depends_on") or "")
        dependent_portfolio = objective_to_portfolio.get(dependent_objective)
        blocker_portfolio = objective_to_portfolio.get(blocker_objective)
        if not dependent_portfolio or not blocker_portfolio or dependent_portfolio == blocker_portfolio:
            continue

        key = (dependent_portfolio, blocker_portfolio)
        if key not in dependency_index:
            dependency_index[key] = {
                "portfolio_id": dependent_portfolio,
                "depends_on_portfolio_id": blocker_portfolio,
                "objective_pairs": [],
                "blocked_dependency_count": 0,
            }

        dep_row = dependency_index[key]
        dep_row["objective_pairs"].append(
            {
                "objective_id": dependent_objective,
                "depends_on": blocker_objective,
            }
        )
        if bool(blocked_map.get(dependent_objective, False)):
            dep_row["blocked_dependency_count"] = int(dep_row["blocked_dependency_count"]) + 1

    cross_portfolio_dependencies = sorted(
        (
            {
                **row,
                "dependency_count": len(row["objective_pairs"]),
            }
            for row in dependency_index.values()
        ),
        key=lambda item: (int(item["blocked_dependency_count"]), int(item["dependency_count"])),
        reverse=True,
    )

    supervisor_runtime = _get_supervisor_runtime(workspace_id)
    available_slots = max(1, int(supervisor_runtime.get("available_slots", 1)))
    active_portfolios = [portfolio for portfolio in objective_portfolios if int(portfolio.get("resource_load", 0)) > 0]
    total_resource_load = sum(int(portfolio.get("resource_load", 0)) for portfolio in active_portfolios)

    portfolio_resource_conflicts: list[dict[str, Any]] = []
    if len(active_portfolios) >= 2 and total_resource_load > available_slots:
        ordered = sorted(
            active_portfolios,
            key=lambda item: (
                int(item.get("resource_load", 0)),
                float(item.get("portfolio_pressure", 0.0)),
                float(item.get("portfolio_risk", 0.0)),
            ),
            reverse=True,
        )
        portfolio_resource_conflicts.append(
            {
                "available_slots": available_slots,
                "total_resource_load": total_resource_load,
                "resource_gap": max(0, total_resource_load - available_slots),
                "contending_portfolios": [
                    {
                        "portfolio_id": str(portfolio["portfolio_id"]),
                        "resource_load": int(portfolio.get("resource_load", 0)),
                        "portfolio_pressure": float(portfolio.get("portfolio_pressure", 0.0)),
                        "portfolio_risk": float(portfolio.get("portfolio_risk", 0.0)),
                    }
                    for portfolio in ordered
                ],
            }
        )

    current_portfolio_ranking = [str(item["portfolio_id"]) for item in portfolio_ranking]
    priority_changed = bool(previous_portfolio_ranking) and (previous_portfolio_ranking != current_portfolio_ranking)

    if priority_changed:
        coordination_recommendations.append(
            {
                "type": "portfolio_priority_changed",
                "action": "review_portfolio_priority_shift",
                "previous_order": previous_portfolio_ranking,
                "current_order": current_portfolio_ranking,
                "reason": "portfolio ranking changed between analyses",
            }
        )

    for dependency in cross_portfolio_dependencies:
        coordination_recommendations.append(
            {
                "type": "portfolio_dependency_detected",
                "action": "sequence_portfolios",
                "portfolio_id": dependency["portfolio_id"],
                "depends_on_portfolio_id": dependency["depends_on_portfolio_id"],
                "blocked_dependency_count": dependency["blocked_dependency_count"],
                "dependency_count": dependency["dependency_count"],
                "reason": "cross-portfolio dependency chain detected",
            }
        )

    for conflict in portfolio_resource_conflicts:
        coordination_recommendations.append(
            {
                "type": "portfolio_resource_conflict_detected",
                "action": "governance_review_portfolio_allocation"
                if governance_band == "escalated"
                else "rebalance_portfolio_allocation",
                "available_slots": conflict["available_slots"],
                "total_resource_load": conflict["total_resource_load"],
                "resource_gap": conflict["resource_gap"],
                "contending_portfolios": conflict["contending_portfolios"],
                "reason": "portfolio load exceeds available runtime slots",
            }
        )

    for portfolio in objective_portfolios:
        if portfolio["portfolio_risk"] >= 0.5:
            coordination_recommendations.append(
                {
                    "type": "portfolio_risk_detected",
                    "action": "governance_review_portfolio" if governance_band == "escalated" else "mitigate_portfolio_risk",
                    "portfolio_id": portfolio["portfolio_id"],
                    "portfolio_risk": portfolio["portfolio_risk"],
                    "reason": "elevated portfolio risk",
                }
            )

        if float(portfolio["portfolio_risk"]) >= float(portfolio_policy["escalation_threshold"]):
            escalate_action = "governance_review_portfolio" if governance_band == "escalated" else "escalate_portfolio"
            if bool(portfolio_policy.get("require_governance_review_for_escalation", False)):
                escalate_action = "governance_review_portfolio"
                governance_review_required.append(
                    {
                        "portfolio_id": portfolio["portfolio_id"],
                        "reason": "policy requires governance review before escalation",
                    }
                )

            coordination_recommendations.append(
                {
                    "type": "portfolio_escalation_recommended",
                    "action": escalate_action,
                    "portfolio_id": portfolio["portfolio_id"],
                    "portfolio_risk": portfolio["portfolio_risk"],
                    "reason": "portfolio risk crossed escalation threshold",
                }
            )

        if (
            float(portfolio["portfolio_risk"]) >= float(portfolio_policy["suspension_threshold"])
            and float(portfolio["portfolio_pressure"]) <= float(portfolio_policy["max_pressure_for_suspension"])
        ):
            suspension_rec = {
                "type": "portfolio_suspension_recommended",
                "action": "suspend_portfolio",
                "portfolio_id": portfolio["portfolio_id"],
                "portfolio_pressure": portfolio["portfolio_pressure"],
                "portfolio_risk": portfolio["portfolio_risk"],
                "reason": "high risk portfolio with low strategic pressure",
            }
            if bool(portfolio_policy.get("suppress_suspend_when_escalated", False)) and governance_band == "escalated":
                suppressed_recommendations.append(
                    {
                        "recommendation": suspension_rec,
                        "reason": "policy_suppressed_escalated_band",
                    }
                )
            else:
                coordination_recommendations.append(suspension_rec)

    recommendations_by_portfolio: dict[str, set[str]] = defaultdict(set)
    for recommendation in coordination_recommendations:
        portfolio_id = str(recommendation.get("portfolio_id") or "")
        if not portfolio_id:
            continue
        recommendations_by_portfolio[portfolio_id].add(str(recommendation.get("type") or ""))

    if str(portfolio_policy.get("conflict_resolution", "")) == "prefer_escalation":
        filtered: list[dict[str, Any]] = []
        for recommendation in coordination_recommendations:
            rec_type = str(recommendation.get("type") or "")
            portfolio_id = str(recommendation.get("portfolio_id") or "")
            has_conflict = (
                portfolio_id
                and "portfolio_escalation_recommended" in recommendations_by_portfolio.get(portfolio_id, set())
                and "portfolio_suspension_recommended" in recommendations_by_portfolio.get(portfolio_id, set())
            )
            if has_conflict and rec_type == "portfolio_suspension_recommended":
                policy_conflicts.append(
                    {
                        "portfolio_id": portfolio_id,
                        "suppressed_type": "portfolio_suspension_recommended",
                        "retained_type": "portfolio_escalation_recommended",
                        "resolution": "prefer_escalation",
                    }
                )
                suppressed_recommendations.append(
                    {
                        "recommendation": recommendation,
                        "reason": "policy_conflict_prefer_escalation",
                    }
                )
                continue
            filtered.append(recommendation)
        coordination_recommendations = filtered

    emitted: list[dict[str, Any]] = []
    emitted.append(
        EVENTS.append(
            "coordinator.priority_ranked",
            {
                "workspace_id": workspace_id,
                "priority_ranking": priority_ranking,
                "reason": reason,
                "actor": actor,
            },
            execution_id=execution_id,
            source=source,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
    )

    emitted.append(
        EVENTS.append(
            "coordinator.portfolio_policy_applied",
            {
                "workspace_id": workspace_id,
                "policy": portfolio_policy,
            },
            execution_id=execution_id,
            source=source,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
    )

    for blocked in blocked_objectives:
        emitted.append(
            EVENTS.append(
                "coordinator.blocked_objective_detected",
                {
                    "workspace_id": workspace_id,
                    "blocked_objective": blocked,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for candidate in merge_candidates:
        emitted.append(
            EVENTS.append(
                "coordinator.merge_candidate_detected",
                {
                    "workspace_id": workspace_id,
                    "candidate": candidate,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for portfolio in objective_portfolios:
        emitted.append(
            EVENTS.append(
                "coordinator.portfolio_created",
                {
                    "workspace_id": workspace_id,
                    "portfolio": portfolio,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

        if portfolio["blocked_objectives"]:
            emitted.append(
                EVENTS.append(
                    "coordinator.portfolio_blocked",
                    {
                        "workspace_id": workspace_id,
                        "portfolio_id": portfolio["portfolio_id"],
                        "blocked_objectives": portfolio["blocked_objectives"],
                    },
                    execution_id=execution_id,
                    source=source,
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
            )

        if float(portfolio["portfolio_risk"]) >= 0.5:
            emitted.append(
                EVENTS.append(
                    "coordinator.portfolio_risk_detected",
                    {
                        "workspace_id": workspace_id,
                        "portfolio_id": portfolio["portfolio_id"],
                        "portfolio_risk": portfolio["portfolio_risk"],
                    },
                    execution_id=execution_id,
                    source=source,
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
            )

        emitted.append(
            EVENTS.append(
                "coordinator.portfolio_health_updated",
                {
                    "workspace_id": workspace_id,
                    "portfolio_id": portfolio["portfolio_id"],
                    "portfolio_health": portfolio["portfolio_health"],
                    "portfolio_risk": portfolio["portfolio_risk"],
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    emitted.append(
        EVENTS.append(
            "coordinator.portfolio_ranked",
            {
                "workspace_id": workspace_id,
                "portfolio_ranking": portfolio_ranking,
            },
            execution_id=execution_id,
            source=source,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
    )

    if priority_changed:
        emitted.append(
            EVENTS.append(
                "coordinator.portfolio_priority_changed",
                {
                    "workspace_id": workspace_id,
                    "previous_order": previous_portfolio_ranking,
                    "current_order": current_portfolio_ranking,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for dependency in cross_portfolio_dependencies:
        emitted.append(
            EVENTS.append(
                "coordinator.portfolio_dependency_detected",
                {
                    "workspace_id": workspace_id,
                    "dependency": dependency,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for conflict in portfolio_resource_conflicts:
        emitted.append(
            EVENTS.append(
                "coordinator.portfolio_resource_conflict_detected",
                {
                    "workspace_id": workspace_id,
                    "conflict": conflict,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for review in governance_review_required:
        emitted.append(
            EVENTS.append(
                "coordinator.portfolio_governance_review_required",
                {
                    "workspace_id": workspace_id,
                    "review": review,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for suppressed in suppressed_recommendations:
        emitted.append(
            EVENTS.append(
                "coordinator.portfolio_recommendation_suppressed",
                {
                    "workspace_id": workspace_id,
                    "suppressed": suppressed,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for conflict in policy_conflicts:
        emitted.append(
            EVENTS.append(
                "coordinator.portfolio_policy_conflict_detected",
                {
                    "workspace_id": workspace_id,
                    "conflict": conflict,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    for rec in coordination_recommendations:
        rec_type = str(rec.get("type", ""))
        if rec_type == "suspension_recommended":
            event_type = "coordinator.suspension_recommended"
        elif rec_type == "escalation_recommended":
            event_type = "coordinator.escalation_recommended"
        elif rec_type == "portfolio_escalation_recommended":
            event_type = "coordinator.portfolio_escalation_recommended"
        elif rec_type == "portfolio_suspension_recommended":
            event_type = "coordinator.portfolio_suspension_recommended"
        else:
            continue

        emitted.append(
            EVENTS.append(
                event_type,
                {
                    "workspace_id": workspace_id,
                    "recommendation": rec,
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    emitted.append(
        EVENTS.append(
            "coordinator.recommendation_created",
            {
                "workspace_id": workspace_id,
                "recommendations": coordination_recommendations,
                "governance_band": governance_band,
                "trust_score": float(trust_state.get("score", 0.5)),
                "scheduler_profile": str(scheduler_policy.get("scheduler_profile", "balanced")),
                "reason": reason,
                "actor": actor,
            },
            execution_id=execution_id,
            source=source,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
    )

    coordinator_state = _get_coordinator_state(workspace_id)
    coordinator_state.update(
        {
            "active_objectives": active_objectives,
            "blocked_objectives": blocked_objectives,
            "objective_dependencies": objective_dependencies,
            "workflow_assignments": workflow_assignments,
            "priority_ranking": priority_ranking,
            "coordination_recommendations": coordination_recommendations,
            "merge_candidates": merge_candidates,
            "objective_portfolios": objective_portfolios,
            "portfolio_ranking": portfolio_ranking,
            "portfolio_health": portfolio_health,
            "cross_portfolio_dependencies": cross_portfolio_dependencies,
            "portfolio_resource_conflicts": portfolio_resource_conflicts,
            "portfolio_policy": portfolio_policy,
            "portfolio_policy_conflicts": policy_conflicts,
            "portfolio_suppressed_recommendations": suppressed_recommendations,
            "updated_at": _utc_now(),
        }
    )

    return coordinator_state, emitted


def _scheduler_profile_order(profile: str) -> list[str]:
    order = ["throughput", "balanced", "fair", "mission_critical"]
    if profile not in order:
        return order
    return order


def _scheduler_profile_neighbor(profile: str, direction: str) -> str:
    order = _scheduler_profile_order(profile)
    index = order.index(profile) if profile in order else order.index("balanced")
    if direction == "escalate":
        return order[min(len(order) - 1, index + 1)]
    if direction == "relax":
        return order[max(0, index - 1)]
    return order[index]


def _apply_scheduler_policy_profile(
    *,
    workspace_id: str,
    next_profile: str,
    reason: str,
    execution_id: str | None,
    source: str,
    correlation_id: str | None,
    actor: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    policy = _get_scheduler_policy(workspace_id)
    current_profile = str(policy.get("scheduler_profile", "balanced"))
    defaults = _scheduler_policy_defaults(next_profile)
    policy.update(
        {
            **defaults,
            "adaptive_mode": bool(policy.get("adaptive_mode", False)),
            "overrides": dict(policy.get("overrides", {})),
            "optimization": dict(policy.get("optimization", {})),
            "updated_at": _utc_now(),
        }
    )

    changed_event = EVENTS.append(
        "agent.scheduler_policy_changed",
        {
            "workspace_id": workspace_id,
            "from_profile": current_profile,
            "to_profile": next_profile,
            "reason": reason,
            "actor": actor,
        },
        execution_id=execution_id,
        source=source,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
    )

    profile_event_type = "agent.scheduler_policy_escalated" if _scheduler_profile_order(next_profile).index(next_profile) > _scheduler_profile_order(current_profile).index(current_profile) else "agent.scheduler_policy_relaxed"
    profile_event = EVENTS.append(
        profile_event_type,
        {
            "workspace_id": workspace_id,
            "from_profile": current_profile,
            "to_profile": next_profile,
            "reason": reason,
            "policy": dict(policy),
        },
        execution_id=execution_id,
        source=source,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
    )

    return policy, changed_event, profile_event


def _maybe_adapt_scheduler_policy(
    *,
    workspace_id: str,
    runtime: dict[str, Any],
    ranked: list[tuple[str, float]],
    trigger: str,
    reason: str,
    execution_id: str | None,
    source: str,
    correlation_id: str | None,
    actor: str,
) -> list[dict[str, Any]]:
    policy = _get_scheduler_policy(workspace_id)
    optimization = policy.get("optimization") if isinstance(policy.get("optimization"), dict) else {}
    cycle = int(runtime.get("cycle", 0))
    if not bool(policy.get("adaptive_mode", False)):
        return [
            EVENTS.append(
                "agent.scheduler_policy_recommended",
                {
                    "workspace_id": workspace_id,
                    "scheduler_profile": str(policy.get("scheduler_profile", "balanced")),
                    "adaptive_mode": False,
                    "reason": "adaptive_mode_disabled",
                    "trigger": trigger,
                    "recommendation": str(policy.get("scheduler_profile", "balanced")),
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        ]

    if not ranked:
        return []

    workflows = _get_workspace_workflows(workspace_id)
    observed = [workflows[workflow_id] for workflow_id, _ in ranked if workflow_id in workflows]
    if not observed:
        return []

    wait_times = [int(workflow.get("workflow_wait_time", 0)) for workflow in observed]
    starvation_scores = [float(workflow.get("starvation_score", 0.0)) for workflow in observed]
    pressure_scores = [float(score) for _, score in ranked]
    average_wait = sum(wait_times) / float(len(wait_times))
    max_wait = max(wait_times)
    max_starvation = max(starvation_scores)
    average_pressure = sum(pressure_scores) / float(len(pressure_scores)) if pressure_scores else 0.0
    available_slots = max(1, int(runtime.get("available_slots", 1)))
    contention = max(0, len(ranked) - available_slots)
    contention_ratio = float(contention) / float(max(1, len(ranked)))

    top_gap = 0.0
    if len(ranked) >= 2:
        top_gap = max(0.0, float(ranked[0][1]) - float(ranked[1][1]))
    smoothed_gap = round(top_gap, 3)

    emitted: list[dict[str, Any]] = []
    if top_gap > 0.2:
        smoothed_gap = round(top_gap * 0.65, 3)
        emitted.append(
            EVENTS.append(
                "agent.scheduler_contention_smoothed",
                {
                    "workspace_id": workspace_id,
                    "cycle": cycle,
                    "top_priority_gap": round(top_gap, 3),
                    "smoothed_gap": smoothed_gap,
                    "reason": "gap_dampening",
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )

    starvation_threshold = max(1, int(policy.get("starvation_threshold", 3)))
    wait_ratio = min(1.0, float(max_wait) / float(starvation_threshold))
    confidence = _clamp01((0.5 * max_starvation) + (0.3 * wait_ratio) + (0.2 * contention_ratio))
    optimization["confidence"] = round(confidence, 3)

    emitted.append(
        EVENTS.append(
            "agent.scheduler_confidence",
            {
                "workspace_id": workspace_id,
                "cycle": cycle,
                "scheduler_profile": str(policy.get("scheduler_profile", "balanced")),
                "confidence": optimization["confidence"],
                "signal_components": {
                    "max_starvation": round(max_starvation, 3),
                    "wait_ratio": round(wait_ratio, 3),
                    "contention_ratio": round(contention_ratio, 3),
                },
            },
            execution_id=execution_id,
            source=source,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
    )

    before_pressure = float(optimization.get("last_average_pressure", average_pressure))
    effectiveness = _clamp01(before_pressure - average_pressure)
    optimization["effectiveness_score"] = round(effectiveness, 3)
    optimization["last_average_pressure"] = round(average_pressure, 3)
    emitted.append(
        EVENTS.append(
            "agent.scheduler_effectiveness_scored",
            {
                "workspace_id": workspace_id,
                "cycle": cycle,
                "before_pressure": round(before_pressure, 3),
                "after_pressure": round(average_pressure, 3),
                "effectiveness": optimization["effectiveness_score"],
            },
            execution_id=execution_id,
            source=source,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
    )

    current_profile = str(policy.get("scheduler_profile", "balanced"))
    next_profile = current_profile
    direction = None

    cycles_since_escalation = max(0, cycle - int(optimization.get("last_escalation_cycle", 0)))
    decay_cycles = max(1, int(optimization.get("decay_cycles", 5)))
    if (
        current_profile != "balanced"
        and contention == 0
        and max_starvation <= 0.6
        and confidence < 0.4
        and cycles_since_escalation >= decay_cycles
    ):
        decayed_profile = _scheduler_profile_neighbor(current_profile, "relax")
        if decayed_profile != current_profile:
            policy, changed_event, profile_event = _apply_scheduler_policy_profile(
                workspace_id=workspace_id,
                next_profile=decayed_profile,
                reason="bounded_decay",
                execution_id=execution_id,
                source=source,
                correlation_id=correlation_id,
                actor=actor,
            )
            optimization["last_relaxation_cycle"] = cycle
            history = optimization.get("optimization_history") if isinstance(optimization.get("optimization_history"), list) else []
            history.append(
                {
                    "cycle": cycle,
                    "change": "decay_relaxed",
                    "from_profile": current_profile,
                    "to_profile": decayed_profile,
                    "effectiveness": optimization["effectiveness_score"],
                }
            )
            optimization["optimization_history"] = history[-25:]
            policy["optimization"] = optimization

            emitted.append(
                EVENTS.append(
                    "agent.scheduler_decay_applied",
                    {
                        "workspace_id": workspace_id,
                        "cycle": cycle,
                        "from_profile": current_profile,
                        "to_profile": decayed_profile,
                        "cycles_since_escalation": cycles_since_escalation,
                        "decay_cycles": decay_cycles,
                    },
                    execution_id=execution_id,
                    source=source,
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                )
            )
            emitted.extend([changed_event, profile_event])
            return emitted

    if max_wait >= int(policy.get("starvation_threshold", 3)) or max_starvation >= 0.8:
        direction = "escalate"
        next_profile = _scheduler_profile_neighbor(current_profile, "escalate")
    elif contention == 0 and average_wait <= 1 and current_profile != "throughput":
        direction = "relax"
        next_profile = _scheduler_profile_neighbor(current_profile, "relax")

    if direction is None or next_profile == current_profile:
        emitted.append(
            EVENTS.append(
                "agent.scheduler_policy_recommended",
                {
                    "workspace_id": workspace_id,
                    "scheduler_profile": current_profile,
                    "adaptive_mode": True,
                    "reason": "policy_stable",
                    "trigger": trigger,
                    "recommendation": current_profile,
                    "confidence": optimization["confidence"],
                    "observed": {
                        "average_wait": round(average_wait, 3),
                        "max_wait": max_wait,
                        "max_starvation": round(max_starvation, 3),
                        "contention": contention,
                        "smoothed_gap": smoothed_gap,
                    },
                },
                execution_id=execution_id,
                source=source,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )
        policy["optimization"] = optimization
        return emitted

    policy, changed_event, profile_event = _apply_scheduler_policy_profile(
        workspace_id=workspace_id,
        next_profile=next_profile,
        reason=reason,
        execution_id=execution_id,
        source=source,
        correlation_id=correlation_id,
        actor=actor,
    )

    if direction == "escalate":
        optimization["last_escalation_cycle"] = cycle
    else:
        optimization["last_relaxation_cycle"] = cycle
    history = optimization.get("optimization_history") if isinstance(optimization.get("optimization_history"), list) else []
    history.append(
        {
            "cycle": cycle,
            "change": "escalated" if direction == "escalate" else "relaxed",
            "from_profile": current_profile,
            "to_profile": next_profile,
            "effectiveness": optimization["effectiveness_score"],
        }
    )
    optimization["optimization_history"] = history[-25:]
    policy["optimization"] = optimization

    recommendation_event = EVENTS.append(
        "agent.scheduler_policy_recommended",
        {
            "workspace_id": workspace_id,
            "scheduler_profile": str(policy.get("scheduler_profile", next_profile)),
            "adaptive_mode": True,
            "reason": reason,
            "trigger": trigger,
            "recommendation": next_profile,
            "confidence": optimization["confidence"],
            "observed": {
                "average_wait": round(average_wait, 3),
                "max_wait": max_wait,
                "max_starvation": round(max_starvation, 3),
                "contention": contention,
                "smoothed_gap": smoothed_gap,
            },
        },
        execution_id=execution_id,
        source=source,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
    )

    emitted.extend([recommendation_event, changed_event, profile_event])
    return emitted


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


def _scheduler_curve_multiplier(curve: str, wait_time: int, fairness_window: int) -> float:
    curve_value = curve.strip().lower()
    window = max(1, fairness_window)
    wait_ratio = min(1.0, float(wait_time) / float(window))

    if curve_value == "exponential":
        return round(1.0 + min(0.5, (wait_ratio**2) * 0.5), 3)
    if curve_value == "weighted":
        return round(1.0 + min(0.35, wait_ratio * 0.35), 3)
    return round(1.0 + min(0.25, wait_ratio * 0.25), 3)


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
    scheduler_policy = _get_scheduler_policy(workspace_id)
    runtime["available_slots"] = max(1, int(available_slots))
    runtime["fairness_window"] = max(1, int(scheduler_policy.get("fairness_window", fairness_window)))
    runtime["starvation_threshold"] = max(1, int(scheduler_policy.get("starvation_threshold", starvation_threshold)))
    runtime["cycle"] = int(runtime.get("cycle", 0)) + 1
    workflows = _get_workspace_workflows(workspace_id)
    cycle = int(runtime["cycle"])
    preemption_policy = str(scheduler_policy.get("preemption_policy", "allowed"))
    fairness_curve = str(scheduler_policy.get("fairness_curve", "linear"))
    starvation_recovery = str(scheduler_policy.get("starvation_recovery", "normal"))

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
        curve_multiplier = _scheduler_curve_multiplier(fairness_curve, wait_time, int(runtime["fairness_window"]))
        starvation_base = {
            "soft": 0.08,
            "normal": 0.15,
            "aggressive": 0.24,
        }.get(starvation_recovery, 0.15)
        aging_bonus = min(0.35, float(wait_time) * 0.05 * curve_multiplier)
        starvation_bonus = starvation_base if wait_time >= int(runtime["starvation_threshold"]) else 0.0
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
    previous_active = [str(wf_id) for wf_id in (runtime.get("active_workflows") or [])]
    target_active: list[str]
    if preemption_policy == "never":
        retained = [workflow_id for workflow_id in previous_active if workflow_id in workflows][: runtime["available_slots"]]
        if len(retained) < runtime["available_slots"]:
            for workflow_id, _ in ranked:
                if workflow_id in retained:
                    continue
                retained.append(workflow_id)
                if len(retained) >= runtime["available_slots"]:
                    break
        target_active = retained
    else:
        target_active = [workflow_id for workflow_id, _ in ranked[: runtime["available_slots"]]]

    prev_set = set(previous_active)
    target_set = set(target_active)

    runtime.update(
        {
            "active_workflows": target_active,
            "updated_at": _utc_now(),
        }
    )
    emitted: list[dict[str, Any]] = []

    policy_event = EVENTS.append(
        "agent.scheduler_policy_applied",
        {
            "workspace_id": workspace_id,
            "scheduler_profile": str(scheduler_policy.get("scheduler_profile", "balanced")),
            "fairness_curve": fairness_curve,
            "starvation_recovery": starvation_recovery,
            "preemption_policy": preemption_policy,
            "fairness_window": int(runtime["fairness_window"]),
            "starvation_threshold": int(runtime["starvation_threshold"]),
            "adaptive_mode": bool(scheduler_policy.get("adaptive_mode", False)),
            "optimization": {
                "decay_cycles": int((scheduler_policy.get("optimization") or {}).get("decay_cycles", 5)),
                "confidence": float((scheduler_policy.get("optimization") or {}).get("confidence", 0.0)),
                "effectiveness_score": float((scheduler_policy.get("optimization") or {}).get("effectiveness_score", 0.0)),
            },
            "available_slots": int(runtime["available_slots"]),
            "cycle": cycle,
        },
        execution_id=execution_id,
        source=source,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
    )
    emitted.append(policy_event)

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
            "scheduler_profile": str(scheduler_policy.get("scheduler_profile", "balanced")),
            "fairness_curve": fairness_curve,
            "starvation_recovery": starvation_recovery,
            "preemption_policy": preemption_policy,
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
        if workflow is None or str(workflow.get("status", "")).lower() == "completed":
            continue
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
        if workflow is None or str(workflow.get("status", "")).lower() == "completed":
            continue
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

    if bool(scheduler_policy.get("adaptive_mode", False)):
        emitted.extend(
            _maybe_adapt_scheduler_policy(
                workspace_id=workspace_id,
                runtime=runtime,
                ranked=ranked,
                trigger=trigger,
                reason=reason,
                execution_id=execution_id,
                source=source,
                correlation_id=correlation_id,
                actor=actor,
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
        "portfolio_group": (str(obj.get("portfolio_group") or "").strip() or None),
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


@app.get("/api/agents/scheduler/policy")
async def get_scheduler_policy(workspace_id: str = "andie-default") -> dict[str, Any]:
    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "policy": _get_scheduler_policy(workspace_id),
    }


@app.post("/api/agents/scheduler/policy")
async def apply_scheduler_policy(request: AgentSchedulerPolicyApplyRequest) -> dict[str, Any]:
    try:
        policy = _apply_scheduler_policy(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    event = EVENTS.append(
        "agent.scheduler_policy_applied",
        {
            "workspace_id": request.workspace_id,
            "scheduler_profile": policy["scheduler_profile"],
            "fairness_curve": policy["fairness_curve"],
            "starvation_recovery": policy["starvation_recovery"],
            "preemption_policy": policy["preemption_policy"],
            "fairness_window": policy["fairness_window"],
            "starvation_threshold": policy["starvation_threshold"],
            "adaptive_mode": bool(policy.get("adaptive_mode", False)),
            "optimization": dict(policy.get("optimization", {})),
            "overrides": dict(request.overrides),
            "actor": request.actor,
            "reason": request.reason,
        },
        execution_id=request.execution_id,
        source=request.source,
        workspace_id=request.workspace_id,
        correlation_id=request.correlation_id,
    )

    await _fanout_event(event)

    return {
        "status": "ok",
        "workspace_id": request.workspace_id,
        "policy": policy,
        "event": event,
    }


@app.get("/api/coordinator/state")
async def get_coordinator_state(workspace_id: str = "andie-default") -> dict[str, Any]:
    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "state": _get_coordinator_state(workspace_id),
    }


@app.get("/api/coordinator/recommendations")
async def get_coordinator_recommendations(workspace_id: str = "andie-default") -> dict[str, Any]:
    state = _get_coordinator_state(workspace_id)
    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "recommendations": list(state.get("coordination_recommendations") or []),
    }


@app.post("/api/coordinator/analyze")
async def analyze_coordinator(request: CoordinatorAnalyzeRequest) -> dict[str, Any]:
    state, emitted_events = _run_coordinator_analysis(
        workspace_id=request.workspace_id,
        reason=request.reason,
        actor=request.actor,
        execution_id=request.execution_id,
        source=request.source,
        correlation_id=request.correlation_id,
    )

    for event in emitted_events:
        await _fanout_event(event)

    return {
        "status": "ok",
        "workspace_id": request.workspace_id,
        "priority_ranking": state.get("priority_ranking") or [],
        "objective_portfolios": state.get("objective_portfolios") or [],
        "portfolio_ranking": state.get("portfolio_ranking") or [],
        "portfolio_health": state.get("portfolio_health") or [],
        "cross_portfolio_dependencies": state.get("cross_portfolio_dependencies") or [],
        "portfolio_resource_conflicts": state.get("portfolio_resource_conflicts") or [],
        "portfolio_policy": state.get("portfolio_policy") or {},
        "portfolio_policy_conflicts": state.get("portfolio_policy_conflicts") or [],
        "portfolio_suppressed_recommendations": state.get("portfolio_suppressed_recommendations") or [],
        "blocked_objectives": state.get("blocked_objectives") or [],
        "merge_candidates": state.get("merge_candidates") or [],
        "recommended_actions": state.get("coordination_recommendations") or [],
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
