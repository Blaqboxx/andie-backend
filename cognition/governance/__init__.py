"""
cognition.governance
====================
Autonomous Governance Layer — STEP 13

Exports the complete public API for the governance layer.

Model classes
-------------
    GovernancePolicy       — named governance rule
    PolicyAction           — ALLOW / REQUIRE_CONSENSUS / REQUIRE_HUMAN / BLOCK
    AutonomyLevel          — AUTONOMOUS / CONSENSUS / HUMAN_APPROVAL / BLOCKED
    AutonomyDecision       — gatekeeper decision with full audit trail
    ApprovalRequest        — pending human or consensus approval
    ApprovalOutcome        — resolved approval result
    ApprovalStatus         — PENDING / APPROVED / REJECTED / TIMED_OUT / SKIPPED
    GovernanceEvent        — observable governance telemetry record
    GovernanceEventType    — all event type constants
    BlockedAttempt         — persistent record of a blocked execution
    PolicyViolation        — persistent record of a policy boundary breach
    PolicyMatch            — matching policy with action and priority

Engine classes
--------------
    PolicyEngine           — register, evaluate, and enforce governance policies
    RiskGatekeeper         — pre-execution safety gate
    AutonomyController     — dynamic trust-score based autonomy management
    ApprovalSystem         — human and consensus approval management
"""

from .governance_models import (
    PolicyAction,
    AutonomyLevel,
    ApprovalStatus,
    GovernanceEventType,
    GovernancePolicy,
    PolicyMatch,
    AutonomyDecision,
    ApprovalRequest,
    ApprovalOutcome,
    GovernanceEvent,
    BlockedAttempt,
    PolicyViolation,
)

from .policy_engine      import PolicyEngine
from .risk_gatekeeper    import RiskGatekeeper
from .autonomy_controller import AutonomyController
from .approval_system    import ApprovalSystem

__all__ = [
    # Model layer
    "PolicyAction",
    "AutonomyLevel",
    "ApprovalStatus",
    "GovernanceEventType",
    "GovernancePolicy",
    "PolicyMatch",
    "AutonomyDecision",
    "ApprovalRequest",
    "ApprovalOutcome",
    "GovernanceEvent",
    "BlockedAttempt",
    "PolicyViolation",
    # Engine layer
    "PolicyEngine",
    "RiskGatekeeper",
    "AutonomyController",
    "ApprovalSystem",
]
