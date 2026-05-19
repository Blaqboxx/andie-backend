"""
STEP 13C — Risk Gatekeeper
===========================
The pre-execution safety gate.  Before ANDIE dispatches any task, the gatekeeper:

    1. Runs a RiskEngine assessment
    2. Evaluates all matching governance policies
    3. Determines the required autonomy level
    4. Issues a GatekeeperDecision (ALLOW / ESCALATE / BLOCK)
    5. Emits a GovernanceEvent to the governance event log
    6. Persists blocked attempts and policy violations to memory

This is the central enforcement point — everything flows through here.

Decision logic
--------------
    policy action → autonomy level:
        ALLOW             → AUTONOMOUS   → approved=True  (no further gates needed)
        REQUIRE_CONSENSUS → CONSENSUS    → approved=False (needs ApprovalSystem)
        REQUIRE_HUMAN     → HUMAN_APPROVAL → approved=False
        BLOCK             → BLOCKED      → approved=False, blocked=True

The gatekeeper also applies simulation recommendations:
    - If SimulationEngine recommends DEFERRED or ABORTED, that escalates the
      autonomy level by one step (cannot exceed BLOCKED).

Governance events
-----------------
Every call to ``gate()`` emits at least one ``GovernanceEvent``.  Events are
stored in the ``MemoryRetriever`` under namespace ``"governance_events"`` so
they are persistent across restarts.

Usage
-----
    gatekeeper = RiskGatekeeper(policy_engine, risk_engine, memory)
    decision   = gatekeeper.gate("deploy_api",
                                  context_tags=["prod", "gpu_pressure"],
                                  node_id="nuc-main",
                                  task_id="wave-03")

    if decision.blocked:
        raise BlockedError(decision.block_reason)
    elif not decision.approved:
        # hand off to ApprovalSystem
        ...
    else:
        execute(decision.adaptations)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cognition.memory import MemoryRetriever
from cognition.prediction import RiskEngine, SimulationEngine, SimulationPathType

from .governance_models import (
    AutonomyDecision, AutonomyLevel, BlockedAttempt, GovernanceEvent,
    GovernanceEventType, PolicyAction, PolicyMatch, PolicyViolation,
)
from .policy_engine import PolicyEngine

_NS_EVENTS     = "governance_events"
_NS_BLOCKED    = "governance_blocked"
_NS_VIOLATIONS = "governance_violations"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _escalate_level(level: AutonomyLevel) -> AutonomyLevel:
    """Move one step toward BLOCKED."""
    order = [
        AutonomyLevel.AUTONOMOUS,
        AutonomyLevel.CONSENSUS,
        AutonomyLevel.HUMAN_APPROVAL,
        AutonomyLevel.BLOCKED,
    ]
    idx = order.index(level)
    return order[min(idx + 1, len(order) - 1)]


class RiskGatekeeper:
    """Central pre-execution enforcement gate.

    Parameters
    ----------
    policy_engine:
        Governance policy evaluator.
    risk_engine:
        RiskEngine for pre-execution assessment.
    memory:
        MemoryRetriever for persisting governance events and blocked attempts.
    simulation_engine:
        Optional SimulationEngine — if provided, simulation result is used to
        escalate the autonomy level when the recommended path is DEFERRED or ABORTED.
    """

    def __init__(
        self,
        policy_engine:    PolicyEngine,
        risk_engine:      RiskEngine,
        memory:           MemoryRetriever,
        simulation_engine: Optional[SimulationEngine] = None,
    ) -> None:
        self._policies  = policy_engine
        self._risk      = risk_engine
        self._mem       = memory
        self._sim       = simulation_engine

    # ── Public API ────────────────────────────────────────────────────────────

    def gate(
        self,
        task:         str,
        context_tags: Optional[List[str]] = None,
        node_id:      Optional[str]       = None,
        agent_id:     Optional[str]       = None,
        task_id:      Optional[str]       = None,
        operation:    str                 = "",
    ) -> AutonomyDecision:
        """Evaluate the governance gate for a task.

        Returns an ``AutonomyDecision`` with ``approved``, ``blocked``, and
        ``adaptations`` set.  Emits governance events and persists blocked
        attempts automatically.
        """
        tags = list(context_tags or [])
        tid  = task_id or f"gate-{uuid.uuid4().hex[:8]}"

        # ── 1. Risk assessment ────────────────────────────────────────────────
        assessment = self._risk.assess(
            task=task, context_tags=tags,
            node_id=node_id, agent_id=agent_id, task_id=tid,
        )
        risk_p = assessment.predicted_failure_probability

        # ── 2. Policy evaluation ──────────────────────────────────────────────
        winning_match  = self._policies.evaluate(task, tags, risk_p, operation)
        all_matches    = self._policies.evaluate_all(task, tags, risk_p, operation)
        autonomy_level = AutonomyLevel.from_policy_action(winning_match.action)

        # ── 3. Simulation escalation (optional) ───────────────────────────────
        sim_path: Optional[str] = None
        adaptations: List[str] = list(assessment.recommended_preemptive_actions)

        if self._sim is not None:
            sim_result = self._sim.simulate(
                task=task, context_tags=tags,
                node_id=node_id, agent_id=agent_id, task_id=tid,
            )
            sim_path = sim_result.recommended_path.value
            for a in sim_result.recommended_adaptations:
                if a not in adaptations:
                    adaptations.append(a)

            # Escalate autonomy if simulation recommends caution
            if sim_result.recommended_path in (
                SimulationPathType.DEFERRED, SimulationPathType.ABORTED
            ):
                escalated = _escalate_level(autonomy_level)
                if escalated.numeric > autonomy_level.numeric:
                    autonomy_level = escalated

        # ── 4. Determine final decision ───────────────────────────────────────
        blocked      = autonomy_level == AutonomyLevel.BLOCKED
        approved     = autonomy_level == AutonomyLevel.AUTONOMOUS
        block_reason = ""

        if blocked:
            block_reason = (
                f"Policy '{winning_match.policy_name}' blocked execution "
                f"(risk={risk_p:.3f}, action={winning_match.action.value})"
            )

        decision = AutonomyDecision(
            task_id=tid,
            task=task,
            context_tags=tags,
            node_id=node_id,
            agent_id=agent_id,
            risk_probability=risk_p,
            simulation_path=sim_path,
            autonomy_level=autonomy_level,
            policy_action=winning_match.action,
            matched_policies=all_matches,
            approved=approved,
            blocked=blocked,
            block_reason=block_reason,
            adaptations=adaptations[:6],
        )

        # ── 5. Persist events and records ─────────────────────────────────────
        self._emit_event(decision, winning_match)
        if blocked:
            self._persist_blocked(decision, all_matches)
        self._persist_violations(decision, all_matches)

        return decision

    # ── Convenience queries ───────────────────────────────────────────────────

    def is_blocked(
        self,
        task:         str,
        context_tags: Optional[List[str]] = None,
        risk:         float               = 0.0,
        operation:    str                 = "",
    ) -> bool:
        """Quick check without full assessment — uses policy engine only."""
        return self._policies.is_blocked(task, list(context_tags or []), risk, operation)

    def governance_events(
        self, task: str = "", n: int = 100
    ) -> List[Dict[str, Any]]:
        """Return recent governance events, optionally filtered by task."""
        all_ev = self._mem._store.all(_NS_EVENTS)
        if task:
            all_ev = [e for e in all_ev if e.get("task") == task]
        return sorted(all_ev, key=lambda e: e.get("timestamp", ""), reverse=True)[:n]

    def blocked_attempts(self, task: str = "", n: int = 50) -> List[Dict[str, Any]]:
        """Return persisted blocked-attempt records."""
        all_b = self._mem._store.all(_NS_BLOCKED)
        if task:
            all_b = [b for b in all_b if b.get("task") == task]
        return sorted(all_b, key=lambda b: b.get("timestamp", ""), reverse=True)[:n]

    def violation_count(self) -> int:
        return self._mem._store.count(_NS_VIOLATIONS)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _emit_event(
        self,
        decision: AutonomyDecision,
        match:    PolicyMatch,
    ) -> None:
        if decision.blocked:
            etype = GovernanceEventType.EXECUTION_BLOCKED
        elif decision.approved:
            etype = GovernanceEventType.EXECUTION_ALLOWED
        elif decision.autonomy_level == AutonomyLevel.CONSENSUS:
            etype = GovernanceEventType.CONSENSUS_REQUIRED
        else:
            etype = GovernanceEventType.HUMAN_APPROVAL_REQUIRED

        event = GovernanceEvent(
            event_type=etype,
            task_id=decision.task_id,
            task=decision.task,
            autonomy_level=decision.autonomy_level.value,
            policy_name=match.policy_name,
            risk=decision.risk_probability,
            detail=decision.block_reason or decision.autonomy_level.value,
        )
        key = f"ev-{decision.task_id}-{_now_iso()}"
        self._mem._store.put(_NS_EVENTS, key, event.to_dict())

        # Always emit POLICY_APPLIED event for the winning policy
        pol_event = GovernanceEvent(
            event_type=GovernanceEventType.POLICY_APPLIED,
            task_id=decision.task_id,
            task=decision.task,
            policy_name=match.policy_name,
            risk=decision.risk_probability,
            detail=f"action={match.action.value}",
        )
        key2 = f"pol-{decision.task_id}-{_now_iso()}"
        self._mem._store.put(_NS_EVENTS, key2, pol_event.to_dict())

    def _persist_blocked(
        self,
        decision: AutonomyDecision,
        matches:  List[PolicyMatch],
    ) -> None:
        attempt = BlockedAttempt(
            task_id=decision.task_id,
            task=decision.task,
            block_reason=decision.block_reason,
            risk_probability=decision.risk_probability,
            autonomy_level=decision.autonomy_level.value,
            context_tags=decision.context_tags,
            matched_policies=[m.policy_name for m in matches],
        )
        key = f"blocked-{decision.task_id}"
        self._mem._store.put(
            _NS_BLOCKED, key,
            {**attempt.model_dump(), "timestamp": _now_iso()},
        )

    def _persist_violations(
        self,
        decision: AutonomyDecision,
        matches:  List[PolicyMatch],
    ) -> None:
        for match in matches:
            if match.action in (PolicyAction.BLOCK, PolicyAction.REQUIRE_HUMAN):
                violation = PolicyViolation(
                    task_id=decision.task_id,
                    task=decision.task,
                    policy_name=match.policy_name,
                    violation=f"action={match.action.value} triggered",
                    risk=decision.risk_probability,
                )
                key = f"viol-{decision.task_id}-{match.policy_name}"
                self._mem._store.put(
                    _NS_VIOLATIONS, key,
                    {**violation.model_dump(), "timestamp": _now_iso()},
                )
