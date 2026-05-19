"""
STEP 13A — Governance Data Models
===================================
Pydantic v2 contracts shared across the entire governance layer.

Model hierarchy
---------------
Policy layer:
    PolicyAction       — what the engine does when a policy matches
    GovernancePolicy   — a named rule (risk threshold, tags, capabilities)
    PolicyMatch        — which policy fired and with what action

Autonomy layer:
    AutonomyLevel      — AUTONOMOUS → CONSENSUS → HUMAN_APPROVAL → BLOCKED
    AutonomyDecision   — final decision for a task with full audit trail

Approval layer:
    ApprovalRequest    — ask for human or consensus sign-off
    ApprovalStatus     — pending / approved / rejected / timed_out
    ApprovalOutcome    — resolved result of an approval request

Governance event layer:
    GovernanceEventType — telemetry event types
    GovernanceEvent    — emitted for every governance decision (observable autonomy)

Governance memory layer:
    BlockedAttempt     — persisted record of a blocked execution
    PolicyViolation    — persisted record of a policy boundary breach
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from pydantic import BaseModel, Field, field_validator
except ImportError:
    from pydantic import BaseModel, Field, validator as field_validator  # type: ignore

from enum import Enum


# ── Enumerations ──────────────────────────────────────────────────────────────

class PolicyAction(str, Enum):
    ALLOW             = "allow"
    REQUIRE_CONSENSUS = "require_consensus"
    REQUIRE_HUMAN     = "require_human"
    BLOCK             = "block"

    @property
    def severity(self) -> int:
        """Higher = more restrictive."""
        return {
            self.ALLOW:             0,
            self.REQUIRE_CONSENSUS: 1,
            self.REQUIRE_HUMAN:     2,
            self.BLOCK:             3,
        }[self]


class AutonomyLevel(str, Enum):
    AUTONOMOUS      = "autonomous"       # execute freely
    CONSENSUS       = "consensus"        # agent consensus required
    HUMAN_APPROVAL  = "human_approval"   # human sign-off required
    BLOCKED         = "blocked"          # do not execute under any circumstances

    @property
    def numeric(self) -> int:
        return {"autonomous": 0, "consensus": 1, "human_approval": 2, "blocked": 3}[self.value]

    @classmethod
    def from_policy_action(cls, action: PolicyAction) -> "AutonomyLevel":
        return {
            PolicyAction.ALLOW:             cls.AUTONOMOUS,
            PolicyAction.REQUIRE_CONSENSUS: cls.CONSENSUS,
            PolicyAction.REQUIRE_HUMAN:     cls.HUMAN_APPROVAL,
            PolicyAction.BLOCK:             cls.BLOCKED,
        }[action]

    @classmethod
    def from_risk(cls, failure_probability: float) -> "AutonomyLevel":
        """Default risk-to-autonomy mapping (tunable via policy)."""
        if failure_probability < 0.35:   return cls.AUTONOMOUS
        if failure_probability < 0.60:   return cls.CONSENSUS
        if failure_probability < 0.80:   return cls.HUMAN_APPROVAL
        return cls.BLOCKED


class ApprovalStatus(str, Enum):
    PENDING     = "pending"
    APPROVED    = "approved"
    REJECTED    = "rejected"
    TIMED_OUT   = "timed_out"
    SKIPPED     = "skipped"    # autonomy level did not require approval


class GovernanceEventType(str, Enum):
    EXECUTION_ALLOWED      = "execution_allowed"
    EXECUTION_BLOCKED      = "execution_blocked"
    CONSENSUS_REQUIRED     = "consensus_required"
    CONSENSUS_PASSED       = "consensus_passed"
    CONSENSUS_FAILED       = "consensus_failed"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"
    HUMAN_APPROVED          = "human_approved"
    HUMAN_REJECTED          = "human_rejected"
    POLICY_APPLIED          = "policy_applied"
    POLICY_VIOLATION        = "policy_violation"
    AUTONOMY_TIGHTENED      = "autonomy_tightened"
    AUTONOMY_RELAXED        = "autonomy_relaxed"


# ── Policy layer ──────────────────────────────────────────────────────────────

class GovernancePolicy(BaseModel):
    """A named governance rule applied to task execution decisions."""
    name:                    str
    description:             str                = ""

    # Matching criteria (any non-empty field that matches triggers the policy)
    match_tags:              List[str]          = Field(default_factory=list,
                                                        description="Task must have ALL of these tags")
    match_tasks:             List[str]          = Field(default_factory=list,
                                                        description="Task names that trigger this policy")
    match_operations:        List[str]          = Field(default_factory=list,
                                                        description="Operation types that trigger this policy")

    # Risk thresholds
    risk_threshold:          float              = Field(default=0.0, ge=0.0, le=1.0,
                                                        description="Minimum risk to trigger this policy")
    max_allowed_risk:        float              = Field(default=1.0, ge=0.0, le=1.0,
                                                        description="Risk above which the action fires")

    # What to do when this policy fires
    action:                  PolicyAction       = PolicyAction.ALLOW

    # Execution constraints
    requires_consensus:      bool               = False
    requires_human_approval: bool               = False
    allowed_capabilities:    List[str]          = Field(default_factory=list)
    blocked_operations:      List[str]          = Field(default_factory=list,
                                                        description="Operation strings that are always blocked")

    # Meta
    priority:                int                = Field(default=0, ge=0,
                                                        description="Higher priority wins when policies conflict")
    enabled:                 bool               = True

    def matches(
        self,
        task:         str,
        tags:         List[str],
        risk:         float,
        operation:    str = "",
    ) -> bool:
        """Return True if this policy applies to the given task/context."""
        if not self.enabled:
            return False
        if not (self.risk_threshold <= risk <= self.max_allowed_risk):
            return False

        # Tag match: ALL listed tags must be present
        if self.match_tags and not all(t in tags for t in self.match_tags):
            return False

        # Task match
        if self.match_tasks and task not in self.match_tasks:
            return False

        # Operation match
        if self.match_operations and operation not in self.match_operations:
            return False

        return True


class PolicyMatch(BaseModel):
    """Result of matching a single policy against a task."""
    policy_name: str
    action:      PolicyAction
    priority:    int
    rationale:   str = ""


# ── Autonomy decision layer ───────────────────────────────────────────────────

class AutonomyDecision(BaseModel):
    """Full governance decision for a task before execution."""
    task_id:      str
    task:         str
    context_tags: List[str] = Field(default_factory=list)
    node_id:      Optional[str] = None
    agent_id:     Optional[str] = None

    # Prediction inputs (snapshot)
    risk_probability:   float
    simulation_path:    Optional[str] = None    # recommended SimulationPathType value

    # Governance outputs
    autonomy_level:    AutonomyLevel
    policy_action:     PolicyAction
    matched_policies:  List[PolicyMatch] = Field(default_factory=list)

    # Execution gate
    approved:          bool  = False            # set to True when all approvals pass
    blocked:           bool  = False
    block_reason:      str   = ""
    adaptations:       List[str] = Field(default_factory=list)

    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def is_executable(self) -> bool:
        return self.approved and not self.blocked

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id":          self.task_id,
            "task":             self.task,
            "risk_probability": self.risk_probability,
            "autonomy_level":   self.autonomy_level.value,
            "policy_action":    self.policy_action.value,
            "approved":         self.approved,
            "blocked":          self.blocked,
            "block_reason":     self.block_reason,
            "adaptations":      self.adaptations,
            "matched_policies": [{"name": m.policy_name, "action": m.action.value}
                                  for m in self.matched_policies],
            "timestamp":        self.timestamp,
        }


# ── Approval layer ────────────────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    """Request for human or consensus approval of a governed task."""
    request_id:   str
    task_id:      str
    task:         str
    reason:       str                  = ""
    autonomy_level: AutonomyLevel
    risk_probability: float
    context:      Dict[str, Any]       = Field(default_factory=dict)
    requestor:    str                  = "governance"
    timestamp:    str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ApprovalOutcome(BaseModel):
    """Resolved result of an approval request."""
    request_id:   str
    task_id:      str
    status:       ApprovalStatus
    approved_by:  Optional[str]   = None   # agent_id, "human", or None
    reason:       str             = ""
    timestamp:    str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def is_approved(self) -> bool:
        return self.status == ApprovalStatus.APPROVED


# ── Governance event layer ────────────────────────────────────────────────────

class GovernanceEvent(BaseModel):
    """Telemetry record emitted for every governance decision.

    These events provide observable autonomy control — every block, approval,
    or policy application is recorded here.
    """
    event_type:   GovernanceEventType
    task_id:      str
    task:         str
    autonomy_level: Optional[str]   = None
    policy_name:  Optional[str]     = None
    risk:         Optional[float]   = None
    detail:       str               = ""
    timestamp:    str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event":          self.event_type.value,
            "task_id":        self.task_id,
            "task":           self.task,
            "autonomy_level": self.autonomy_level,
            "policy_name":    self.policy_name,
            "risk":           self.risk,
            "detail":         self.detail,
            "timestamp":      self.timestamp,
        }


# ── Governance memory records ─────────────────────────────────────────────────

class BlockedAttempt(BaseModel):
    """Persistent record of a blocked execution — institutional memory."""
    task_id:         str
    task:            str
    block_reason:    str
    risk_probability: float
    autonomy_level:  str
    context_tags:    List[str] = Field(default_factory=list)
    matched_policies: List[str] = Field(default_factory=list)
    timestamp:       str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PolicyViolation(BaseModel):
    """Record of an attempt that violated a specific policy boundary."""
    task_id:      str
    task:         str
    policy_name:  str
    violation:    str
    risk:         float
    timestamp:    str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
