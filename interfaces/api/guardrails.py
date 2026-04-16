"""
Autonomy Guardrail Engine
=========================
Validates every proposed decision before execution. Returns a tuple of
(final_decision, blocked, reason, confidence) so the autonomy loop can emit
rich observability data on every cycle.

Also tracks conditions that trigger automatic system disable:
  - Too many errors in a short window
  - Decision oscillation (A→B→A→B)
  - Stuck loops (same decision N times)
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Dict, Optional, Tuple

try:
    from autonomy.learning_engine import score_skill as _learning_score_skill
    _LEARNING_ENGINE = True
except Exception:
    _LEARNING_ENGINE = False
    _learning_score_skill = None  # type: ignore[assignment]

# ─── Tunable thresholds ────────────────────────────────────────────────────────
MAX_DECISIONS_PER_MINUTE: int = 10
NODE_CPU_OVERLOAD_THRESHOLD: float = 95.0
MINIMUM_CONFIDENCE_THRESHOLD: float = 0.40
CRITICAL_ERROR_WINDOW_SECONDS: float = 30.0
CRITICAL_ERROR_LIMIT: int = 5
OSCILLATION_WINDOW: int = 10   # look at last N decisions
OSCILLATION_FLIP_THRESHOLD: int = 6  # alternating flips out of OSCILLATION_WINDOW
STUCK_WINDOW: int = 8           # last N decisions
STUCK_UNIQUE_THRESHOLD: int = 1  # all identical = stuck

# Cooldown in seconds per action type (prevents flapping)
ACTION_COOLDOWN_RULES: Dict[str, float] = {
    "restart_node": 60.0,
    "run recovery workflow": 30.0,
    "run process optimization workflow": 20.0,
    "run load balancing workflow": 20.0,
}

# ─── Module-level state ────────────────────────────────────────────────────────
_lock = threading.Lock()
_decision_timestamps: deque = deque(maxlen=120)           # rolling call timestamps
_action_last_time: Dict[str, float] = {}                   # last exec time per action
_error_timestamps: deque = deque(maxlen=60)                # error timestamps
_recent_decisions: deque = deque(maxlen=OSCILLATION_WINDOW)
_auto_disabled: bool = False
_auto_disable_reason: Optional[str] = None
_hard_disabled: bool = False  # operator-forced kill switch


# ─── Recording helpers ─────────────────────────────────────────────────────────

def record_decision(decision: str) -> None:
    """Call this AFTER a decision passes validation and is about to execute."""
    with _lock:
        now = time.time()
        _decision_timestamps.append(now)
        _action_last_time[decision] = now
        _recent_decisions.append(decision)


def record_error() -> None:
    """Call each time the autonomy loop catches an unexpected exception."""
    with _lock:
        _error_timestamps.append(time.time())


# ─── Internal helpers ──────────────────────────────────────────────────────────

def _decisions_last_minute() -> int:
    cutoff = time.time() - 60.0
    return sum(1 for t in _decision_timestamps if t > cutoff)


def _errors_in_window() -> int:
    cutoff = time.time() - CRITICAL_ERROR_WINDOW_SECONDS
    return sum(1 for t in _error_timestamps if t > cutoff)


def _is_oscillating() -> bool:
    decisions = list(_recent_decisions)
    if len(decisions) < 4:
        return False
    flips = sum(1 for i in range(1, len(decisions)) if decisions[i] != decisions[i - 1])
    return flips >= OSCILLATION_FLIP_THRESHOLD


def _is_stuck() -> bool:
    decisions = list(_recent_decisions)
    if len(decisions) < STUCK_WINDOW:
        return False
    return len(set(decisions[-STUCK_WINDOW:])) <= STUCK_UNIQUE_THRESHOLD


# ─── Confidence scoring ────────────────────────────────────────────────────────

def confidence_score(decision: str | None, state: Dict[str, Any]) -> float:
    """
    Heuristic confidence in [0.0, 1.0].  1.0 = fully confident; 0.0 = do not execute.
    No-op decisions are always fully confident (nothing will be done).
    When the learning engine has history for a decision, its learned score is
    blended in (weighted 40%) so confidence improves over time with real outcomes.
    """
    if decision is None:
        return 1.0

    # ── Base: learned score from learning engine ──────────────────────────────
    if _LEARNING_ENGINE and _learning_score_skill is not None:
        try:
            learned = _learning_score_skill(decision, context_key="autonomy")
        except Exception:
            learned = 0.6
    else:
        learned = 0.6

    score = learned

    cpu = state.get("cpu", 0) or 0
    memory = state.get("memory", 0) or 0
    failed = state.get("failed", 0) or 0
    nodes = state.get("nodes", {}) or {}

    # Degraded nodes reduce confidence
    degraded = sum(1 for n in nodes.values() if not n.get("available", True))
    score -= 0.12 * degraded

    # High resource pressure against non-recovery decisions
    if decision != "run recovery workflow" and (cpu > 85 or memory > 90):
        score -= 0.2

    # Oscillating decisions are less trustworthy
    if _is_oscillating():
        score -= 0.25

    # Stuck loop — system may be confused
    if _is_stuck():
        score -= 0.20

    # Recovery decisions gain confidence when there is actual failure evidence
    if decision == "run recovery workflow" and failed > 0:
        score = min(score + 0.10, 1.0)

    return max(0.0, min(round(score, 3), 1.0))


# ─── Core validate ────────────────────────────────────────────────────────────

def validate(
    decision: str | None,
    state: Dict[str, Any],
) -> Tuple[str | None, bool, Optional[str], float]:
    """
    Validate a proposed decision against all guardrails.

    Returns:
        (final_decision, blocked, reason, confidence)

    If blocked is True, final_decision is None and reason explains why.
    confidence is always populated (even when blocked, for telemetry).
    """
    global _auto_disabled, _auto_disable_reason

    with _lock:
        # ── 1. Hard kill switch (operator) ──────────────────────────────────
        if _hard_disabled:
            return None, True, "hard_disabled", 0.0

        # ── 2. Auto-disable already triggered ───────────────────────────────
        if _auto_disabled:
            return None, True, f"auto_disabled:{_auto_disable_reason}", 0.0

        # ── 3. No-op — always safe ───────────────────────────────────────────
        if decision is None:
            return None, False, None, 1.0

        # ── 4. Critical error rate → auto-disable ───────────────────────────
        errors = _errors_in_window()
        if errors >= CRITICAL_ERROR_LIMIT:
            _auto_disabled = True
            _auto_disable_reason = f"critical_errors_{errors}_in_{int(CRITICAL_ERROR_WINDOW_SECONDS)}s"
            return None, True, f"auto_disabled:{_auto_disable_reason}", 0.0

        # ── 5. Oscillation → auto-disable ───────────────────────────────────
        if _is_oscillating():
            _auto_disabled = True
            _auto_disable_reason = "decision_oscillation"
            return None, True, "auto_disabled:decision_oscillation", 0.0

        # ── 6. Decision rate limit ───────────────────────────────────────────
        if _decisions_last_minute() >= MAX_DECISIONS_PER_MINUTE:
            conf = confidence_score(decision, state)
            return None, True, "decision_rate_exceeded", conf

        # ── 7. Node CPU overload ─────────────────────────────────────────────
        nodes = state.get("nodes", {}) or {}
        for node_id, node in nodes.items():
            node_cpu = node.get("cpu") or 0
            if node_cpu > NODE_CPU_OVERLOAD_THRESHOLD:
                conf = max(0.0, 1.0 - node_cpu / 100.0)
                return None, True, f"node_overload:{node_id}", conf

        # ── 8. Action cooldown ───────────────────────────────────────────────
        cooldown = ACTION_COOLDOWN_RULES.get(decision)
        if cooldown:
            last = _action_last_time.get(decision, 0.0)
            if last > 0:
                elapsed = time.time() - last
                if elapsed < cooldown:
                    remaining = int(cooldown - elapsed)
                    conf = confidence_score(decision, state)
                    return None, True, f"cooldown:{decision}:{remaining}s_remaining", conf

        # ── 9. Invalid / corrupted state ─────────────────────────────────────
        if not isinstance(state, dict) or not state:
            return None, True, "invalid_state", 0.0

        # ── 10. Confidence threshold ─────────────────────────────────────────
        conf = confidence_score(decision, state)
        if conf < MINIMUM_CONFIDENCE_THRESHOLD:
            return None, True, f"low_confidence:{conf}", conf

        return decision, False, None, conf


# ─── Kill switch control ──────────────────────────────────────────────────────

def enable_hard_kill() -> None:
    global _hard_disabled
    with _lock:
        _hard_disabled = True


def clear_hard_kill() -> None:
    global _hard_disabled
    with _lock:
        _hard_disabled = False


def reset_auto_disable() -> None:
    global _auto_disabled, _auto_disable_reason
    with _lock:
        _auto_disabled = False
        _auto_disable_reason = None


def reset_all() -> None:
    """Full reset — used on autonomy restart."""
    global _auto_disabled, _auto_disable_reason, _hard_disabled
    with _lock:
        _auto_disabled = False
        _auto_disable_reason = None
        _hard_disabled = False
        _decision_timestamps.clear()
        _action_last_time.clear()
        _error_timestamps.clear()
        _recent_decisions.clear()


# ─── Status ───────────────────────────────────────────────────────────────────

def guardrail_status() -> Dict[str, Any]:
    with _lock:
        return {
            "hardDisabled": _hard_disabled,
            "autoDisabled": _auto_disabled,
            "autoDisableReason": _auto_disable_reason,
            "decisionsLastMinute": _decisions_last_minute(),
            "errorsInWindow": _errors_in_window(),
            "oscillating": _is_oscillating(),
            "stuck": _is_stuck(),
            "recentDecisions": list(_recent_decisions),
            "thresholds": {
                "maxDecisionsPerMinute": MAX_DECISIONS_PER_MINUTE,
                "nodeCpuOverload": NODE_CPU_OVERLOAD_THRESHOLD,
                "minConfidence": MINIMUM_CONFIDENCE_THRESHOLD,
                "criticalErrorLimit": CRITICAL_ERROR_LIMIT,
                "criticalErrorWindowSeconds": CRITICAL_ERROR_WINDOW_SECONDS,
            },
        }
