"""
STEP 13D — Autonomy Controller
================================
Dynamically adjusts ANDIE's autonomy level based on operational track record.

This module answers: *"How autonomous should ANDIE be right now?"*

The controller maintains a **trust score** (0–1) that represents ANDIE's recent
governance track record.  The trust score is updated by:

    +0.05 per approved & successful execution (within the controller's window)
    -0.10 per blocked attempt
    -0.07 per policy violation
    -0.15 per human override (human rejected a CONSENSUS decision)

Trust score → Autonomy band
---------------------------
    ≥ 0.80  → full AUTONOMOUS operations permitted
    ≥ 0.55  → CONSENSUS required for high-risk
    ≥ 0.30  → HUMAN_APPROVAL required
    < 0.30  → BLOCKED — governance lockdown until trust is rebuilt

Dynamic tightening / relaxing
------------------------------
The controller can be explicitly tightened by external signals (e.g. Sentinel
raises a security alert) or relaxed by a human operator.

    controller.tighten("sentinel_alert: suspicious activity")
    controller.relax()

Each tightening reduces the trust score by ``_TIGHTEN_STEP`` (default 0.20).
Relaxing restores it by ``_RELAX_STEP`` (default 0.10) up to the historical max.

Governance memory integration
------------------------------
The controller reads from ``MemoryRetriever``:
    - Blocked attempts count (recent window)
    - Governance events (to recompute trust)

It emits AUTONOMY_TIGHTENED / AUTONOMY_RELAXED governance events.

Usage
-----
    controller = AutonomyController(memory)

    level = controller.current_level()   # current autonomy band
    score = controller.trust_score()     # 0-1 numeric trust

    # After a bad event:
    controller.record_violation("dangerous_deploy blocked by policy")
    controller.record_block("deploy_prod", "risk=0.95")

    # After a successful run:
    controller.record_success("deploy_api")
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cognition.memory import MemoryRetriever

from .governance_models import (
    AutonomyLevel, GovernanceEvent, GovernanceEventType,
)

# Trust score adjustments
_SUCCESS_REWARD  =  0.05
_BLOCK_PENALTY   = -0.10
_VIOLATION_PENALTY = -0.07
_OVERRIDE_PENALTY = -0.15

# Dynamic tighten / relax steps
_TIGHTEN_STEP  = 0.20
_RELAX_STEP    = 0.10

# Trust score → autonomy band
_TRUST_BANDS = [
    (0.80, AutonomyLevel.AUTONOMOUS),
    (0.55, AutonomyLevel.CONSENSUS),
    (0.30, AutonomyLevel.HUMAN_APPROVAL),
    (0.00, AutonomyLevel.BLOCKED),
]

_NS_AUTONOMY = "autonomy_state"
_KEY_SCORE   = "trust_score"

_NS_EVENTS   = "governance_events"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutonomyController:
    """Dynamically manages ANDIE's current autonomy level via a trust score.

    Parameters
    ----------
    memory:
        MemoryRetriever — trust score is persisted here across restarts.
    initial_trust:
        Starting trust score (default 0.75 → AUTONOMOUS band).
    """

    def __init__(
        self,
        memory:        MemoryRetriever,
        initial_trust: float = 0.75,
    ) -> None:
        self._mem    = memory
        self._load_or_init(initial_trust)

    # ── Trust score management ────────────────────────────────────────────────

    def trust_score(self) -> float:
        rec = self._mem._store.get(_NS_AUTONOMY, _KEY_SCORE)
        return rec.get("score", 0.75) if rec else 0.75

    def current_level(self) -> AutonomyLevel:
        score = self.trust_score()
        for threshold, level in _TRUST_BANDS:
            if score >= threshold:
                return level
        return AutonomyLevel.BLOCKED

    # ── Record outcomes ───────────────────────────────────────────────────────

    def record_success(self, task: str) -> float:
        """Reward a successful governed execution.  Returns new trust score."""
        return self._adjust(_SUCCESS_REWARD, f"success: {task}")

    def record_block(self, task: str, reason: str = "") -> float:
        """Penalise a blocked execution attempt.  Returns new trust score."""
        score = self._adjust(_BLOCK_PENALTY, f"blocked: {task} — {reason}")
        self._emit(GovernanceEventType.AUTONOMY_TIGHTENED,
                   task, f"block penalty: {reason}")
        return score

    def record_violation(self, detail: str = "") -> float:
        """Penalise a policy violation.  Returns new trust score."""
        return self._adjust(_VIOLATION_PENALTY, f"violation: {detail}")

    def record_human_override(self, task: str) -> float:
        """Human rejected a consensus decision — heavy penalty."""
        score = self._adjust(_OVERRIDE_PENALTY, f"human_override: {task}")
        self._emit(GovernanceEventType.AUTONOMY_TIGHTENED,
                   task, "human overrode autonomous decision")
        return score

    # ── Dynamic tighten / relax ───────────────────────────────────────────────

    def tighten(self, reason: str = "") -> AutonomyLevel:
        """Explicitly reduce trust (e.g. Sentinel raises an alert)."""
        self._adjust(-_TIGHTEN_STEP, f"tighten: {reason}")
        level = self.current_level()
        self._emit(GovernanceEventType.AUTONOMY_TIGHTENED,
                   "_system", f"explicit tighten — {reason}")
        return level

    def relax(self, reason: str = "") -> AutonomyLevel:
        """Restore trust by one step (operator action)."""
        self._adjust(_RELAX_STEP, f"relax: {reason}")
        level = self.current_level()
        self._emit(GovernanceEventType.AUTONOMY_RELAXED,
                   "_system", f"explicit relax — {reason}")
        return level

    def set_trust(self, score: float) -> None:
        """Override trust score directly (operator or test)."""
        clamped = max(0.0, min(1.0, score))
        self._save(clamped)

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        score = self.trust_score()
        level = self.current_level()
        rec   = self._mem._store.get(_NS_AUTONOMY, _KEY_SCORE) or {}
        return {
            "trust_score":    round(score, 4),
            "autonomy_level": level.value,
            "total_successes": rec.get("successes", 0),
            "total_blocks":    rec.get("blocks", 0),
            "total_violations": rec.get("violations", 0),
            "total_overrides": rec.get("overrides", 0),
            "last_updated":    rec.get("last_updated", ""),
        }

    def history(self, n: int = 50) -> List[Dict[str, Any]]:
        """Return recent autonomy-related governance events."""
        ev = self._mem._store.all(_NS_EVENTS)
        autonomy_ev = [
            e for e in ev
            if e.get("event") in (
                GovernanceEventType.AUTONOMY_TIGHTENED.value,
                GovernanceEventType.AUTONOMY_RELAXED.value,
            )
        ]
        return sorted(autonomy_ev, key=lambda e: e.get("timestamp", ""), reverse=True)[:n]

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _adjust(self, delta: float, note: str) -> float:
        rec   = self._mem._store.get(_NS_AUTONOMY, _KEY_SCORE) or {}
        score = float(rec.get("score", 0.75)) + delta
        score = round(max(0.0, min(1.0, score)), 4)

        # Track counters
        if delta > 0:
            rec["successes"] = rec.get("successes", 0) + 1
        elif delta == _BLOCK_PENALTY:
            rec["blocks"] = rec.get("blocks", 0) + 1
        elif delta == _VIOLATION_PENALTY:
            rec["violations"] = rec.get("violations", 0) + 1
        elif delta == _OVERRIDE_PENALTY:
            rec["overrides"] = rec.get("overrides", 0) + 1

        rec["score"]        = score
        rec["last_note"]    = note
        rec["last_updated"] = _now_iso()
        self._mem._store.put(_NS_AUTONOMY, _KEY_SCORE, rec)
        return score

    def _save(self, score: float) -> None:
        rec = self._mem._store.get(_NS_AUTONOMY, _KEY_SCORE) or {}
        rec["score"]        = score
        rec["last_updated"] = _now_iso()
        self._mem._store.put(_NS_AUTONOMY, _KEY_SCORE, rec)

    def _load_or_init(self, initial: float) -> None:
        if not self._mem._store.exists(_NS_AUTONOMY, _KEY_SCORE):
            self._mem._store.put(_NS_AUTONOMY, _KEY_SCORE, {
                "score":       round(max(0.0, min(1.0, initial)), 4),
                "successes":   0,
                "blocks":      0,
                "violations":  0,
                "overrides":   0,
                "last_updated": _now_iso(),
            })

    def _emit(
        self,
        etype:  GovernanceEventType,
        task:   str,
        detail: str,
    ) -> None:
        event = GovernanceEvent(
            event_type=etype,
            task_id=f"autonomy-{_now_iso()}",
            task=task,
            autonomy_level=self.current_level().value,
            risk=None,
            detail=f"{detail} | trust={self.trust_score():.3f}",
        )
        key = f"aut-{_now_iso()}"
        self._mem._store.put(_NS_EVENTS, key, event.to_dict())
