"""
STEP 13E — Approval System
============================
Manages human and consensus approvals for governed task execution.

The approval system sits between the RiskGatekeeper (which identifies *that*
approval is needed) and the actual task executor (which needs the final yes/no).

Two approval paths
------------------

Consensus approval (AutonomyLevel.CONSENSUS)
    Agent-level voting.  ``simulate_consensus`` lets any number of synthetic
    participants vote.  Requires a simple majority.

Human approval (AutonomyLevel.HUMAN_APPROVAL)
    In production, an external system calls ``resolve()`` with the human
    decision.  In testing, ``simulate_human_approval()`` auto-approves or
    auto-rejects based on a risk threshold.

Storage
-------
All pending and resolved approvals are stored in the MemoryRetriever under
namespace ``"approval_history"`` keyed by ``request_id``.

Usage
-----
    approval = ApprovalSystem(memory)

    # Ask for consensus
    req = approval.request(
        task_id="wave-03",
        task="deploy_api",
        autonomy_level=AutonomyLevel.CONSENSUS,
        risk_probability=0.62,
    )

    # Simulate agent votes (testing / headless mode)
    outcome = approval.simulate_consensus(req, participant_count=3)
    print(outcome.is_approved())   # True / False

    # In production — human calls this externally
    outcome = approval.resolve(req.request_id, ApprovalStatus.APPROVED,
                               approved_by="ops_engineer", reason="LGTM")
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cognition.memory import MemoryRetriever

from .governance_models import (
    ApprovalOutcome, ApprovalRequest, ApprovalStatus, AutonomyLevel,
    GovernanceEvent, GovernanceEventType,
)

_NS_APPROVALS = "approval_history"
_NS_EVENTS    = "governance_events"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalSystem:
    """Manages human and consensus approvals for governed task execution.

    Parameters
    ----------
    memory:
        MemoryRetriever — approval history is persisted here.
    auto_approve_threshold:
        Risk probability below which ``simulate_human_approval`` auto-approves
        (default 0.70).  Above this threshold it auto-rejects.
    """

    def __init__(
        self,
        memory:                  MemoryRetriever,
        auto_approve_threshold:  float = 0.70,
    ) -> None:
        self._mem       = memory
        self._threshold = auto_approve_threshold

    # ── Request creation ──────────────────────────────────────────────────────

    def request(
        self,
        task_id:          str,
        task:             str,
        autonomy_level:   AutonomyLevel,
        risk_probability: float,
        reason:           str              = "",
        context:          Optional[Dict[str, Any]] = None,
        requestor:        str              = "governance",
    ) -> ApprovalRequest:
        """Create and persist an approval request.

        Returns the ``ApprovalRequest`` which must be passed to ``resolve()``
        or one of the simulate helpers.
        """
        req = ApprovalRequest(
            request_id=f"req-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            task=task,
            reason=reason,
            autonomy_level=autonomy_level,
            risk_probability=risk_probability,
            context=context or {},
            requestor=requestor,
        )
        self._save_request(req)
        self._emit(
            GovernanceEventType.HUMAN_APPROVAL_REQUIRED
            if autonomy_level == AutonomyLevel.HUMAN_APPROVAL
            else GovernanceEventType.CONSENSUS_REQUIRED,
            req,
        )
        return req

    # ── Resolution ────────────────────────────────────────────────────────────

    def resolve(
        self,
        request_id:  str,
        status:      ApprovalStatus,
        approved_by: Optional[str] = None,
        reason:      str           = "",
    ) -> ApprovalOutcome:
        """Resolve a pending approval request.

        This is the real production path — called by a human operator or an
        external consensus engine.
        """
        outcome = ApprovalOutcome(
            request_id=request_id,
            task_id=self._task_id_for(request_id),
            status=status,
            approved_by=approved_by,
            reason=reason,
        )
        self._save_outcome(request_id, outcome)

        etype = (
            GovernanceEventType.HUMAN_APPROVED
            if status == ApprovalStatus.APPROVED
            else GovernanceEventType.HUMAN_REJECTED
        )
        req_dict = self._load_request(request_id)
        if req_dict:
            fake_req = ApprovalRequest(
                request_id=request_id,
                task_id=req_dict.get("task_id", request_id),
                task=req_dict.get("task", ""),
                autonomy_level=AutonomyLevel(req_dict.get("autonomy_level", AutonomyLevel.AUTONOMOUS.value)),
                risk_probability=req_dict.get("risk_probability", 0.0),
                requestor=req_dict.get("requestor", "governance"),
            )
            self._emit_outcome(etype, fake_req, outcome)

        return outcome

    # ── Simulation helpers (testing / headless mode) ──────────────────────────

    def simulate_human_approval(
        self,
        req: ApprovalRequest,
    ) -> ApprovalOutcome:
        """Auto-approve/reject based on the auto_approve_threshold."""
        approved = req.risk_probability < self._threshold
        status   = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        return self.resolve(
            req.request_id,
            status=status,
            approved_by="auto_sim",
            reason=f"simulated: risk={req.risk_probability:.3f} threshold={self._threshold:.2f}",
        )

    def simulate_consensus(
        self,
        req:               ApprovalRequest,
        participant_count: int   = 3,
        agreement_ratio:   float = 0.6,
    ) -> ApprovalOutcome:
        """Simulate agent consensus voting.

        Parameters
        ----------
        participant_count:
            Number of synthetic voting agents.
        agreement_ratio:
            Fraction of agents that agree with approve-if-risk-<-threshold rule.
            Default 0.6 → majority votes.
        """
        # Count simulated approving votes
        votes_approve = 0
        for _ in range(participant_count):
            # Each agent independently evaluates: approve if risk < threshold
            if req.risk_probability < self._threshold:
                votes_approve += 1

        majority    = participant_count / 2.0
        approved    = votes_approve > majority
        status      = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        outcome = ApprovalOutcome(
            request_id=req.request_id,
            task_id=req.task_id,
            status=status,
            approved_by=f"consensus:{votes_approve}/{participant_count}",
            reason=(
                f"consensus simulation: {votes_approve}/{participant_count} approved "
                f"(threshold={self._threshold:.2f})"
            ),
        )
        self._save_outcome(req.request_id, outcome)

        etype = (
            GovernanceEventType.CONSENSUS_PASSED
            if approved
            else GovernanceEventType.CONSENSUS_FAILED
        )
        self._emit_outcome(etype, req, outcome)

        return outcome

    # ── History & analytics ───────────────────────────────────────────────────

    def approval_history(self, task: str = "", n: int = 50) -> List[Dict[str, Any]]:
        """Return resolved approval records, optionally filtered by task name."""
        records = self._mem._store.all(_NS_APPROVALS)
        if task:
            records = [r for r in records if r.get("task") == task]
        # Only resolved outcomes
        outcomes = [r for r in records if "status" in r and r.get("status") != "pending"]
        return sorted(outcomes, key=lambda r: r.get("timestamp", ""), reverse=True)[:n]

    def approval_rate(self, task: str) -> float:
        """Fraction of approval requests for this task that were approved."""
        hist = self.approval_history(task)
        if not hist:
            return 0.0
        approved = sum(1 for h in hist if h.get("status") == ApprovalStatus.APPROVED.value)
        return round(approved / len(hist), 3)

    def pending_requests(self) -> List[Dict[str, Any]]:
        """Return requests that have not yet been resolved."""
        records = self._mem._store.all(_NS_APPROVALS)
        return [r for r in records if r.get("status") == "pending"]

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _save_request(self, req: ApprovalRequest) -> None:
        record = {
            "request_id":      req.request_id,
            "task_id":         req.task_id,
            "task":            req.task,
            "autonomy_level":  req.autonomy_level.value,
            "risk_probability": req.risk_probability,
            "reason":          req.reason,
            "requestor":       req.requestor,
            "status":          "pending",
            "timestamp":       req.timestamp,
        }
        self._mem._store.put(_NS_APPROVALS, req.request_id, record)

    def _save_outcome(self, request_id: str, outcome: ApprovalOutcome) -> None:
        # Merge outcome into the existing request record
        existing = self._mem._store.get(_NS_APPROVALS, request_id) or {}
        existing.update({
            "status":      outcome.status.value,
            "approved_by": outcome.approved_by,
            "outcome_reason": outcome.reason,
            "outcome_timestamp": outcome.timestamp,
        })
        self._mem._store.put(_NS_APPROVALS, request_id, existing)

    def _load_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._mem._store.get(_NS_APPROVALS, request_id)

    def _task_id_for(self, request_id: str) -> str:
        rec = self._mem._store.get(_NS_APPROVALS, request_id)
        return rec.get("task_id", request_id) if rec else request_id

    def _emit(self, etype: GovernanceEventType, req: ApprovalRequest) -> None:
        event = GovernanceEvent(
            event_type=etype,
            task_id=req.task_id,
            task=req.task,
            autonomy_level=req.autonomy_level.value,
            risk=req.risk_probability,
            detail=f"request_id={req.request_id}",
        )
        self._mem._store.put(_NS_EVENTS, f"appr-{req.request_id}", event.to_dict())

    def _emit_outcome(
        self,
        etype:   GovernanceEventType,
        req:     ApprovalRequest,
        outcome: ApprovalOutcome,
    ) -> None:
        event = GovernanceEvent(
            event_type=etype,
            task_id=req.task_id,
            task=req.task,
            autonomy_level=req.autonomy_level.value,
            risk=req.risk_probability,
            detail=f"approved_by={outcome.approved_by} reason={outcome.reason[:80]}",
        )
        self._mem._store.put(_NS_EVENTS, f"res-{outcome.request_id}", event.to_dict())
