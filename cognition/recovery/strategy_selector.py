"""
Strategy Selector — procedural adaptation logic for ANDIE's recovery subsystem.

Selects the optimal RecoveryStrategy given the full RetryContext.  Priority order:

  1. Pattern intelligence (reflection history recommendation)
  2. Stderr / failure-reason keyword analysis
  3. Epistemic confidence thresholds
  4. Attempt-count escalation
  5. Default: NONE (let the build loop handle it naturally)

The selector is stateless — it never mutates context.  All decisions are
logged so the RetryEngine can persist them for future learning.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from .recovery_models import RecoveryStrategy, RetryContext


# ---------------------------------------------------------------------------
# Lookup tables (ordered: first match wins)
# ---------------------------------------------------------------------------

# (pattern, strategy) — applied against stderr + failure_reason (case-insensitive)
_STDERR_RULES: List[Tuple[str, RecoveryStrategy]] = [
    # Dependency problems
    ("ModuleNotFoundError",          RecoveryStrategy.INSTALL_DEPS),
    ("No module named",              RecoveryStrategy.INSTALL_DEPS),
    ("ImportError",                  RecoveryStrategy.INSTALL_DEPS),
    ("pkg_resources.DistributionNotFound", RecoveryStrategy.INSTALL_DEPS),
    # Syntax / code quality problems
    ("SyntaxError",                  RecoveryStrategy.LLM_REGEN),
    ("IndentationError",             RecoveryStrategy.LLM_REGEN),
    ("# LLM error",                  RecoveryStrategy.LLM_REGEN),
    ("# LLM unavailable",            RecoveryStrategy.LLM_REGEN),
    ("NameError",                    RecoveryStrategy.LLM_REGEN),
    ("AttributeError",               RecoveryStrategy.LLM_REGEN),
    # Timeout problems
    ("TimeoutExpired",               RecoveryStrategy.INCREASE_TIMEOUT),
    ("timed out",                    RecoveryStrategy.INCREASE_TIMEOUT),
    ("Timeout",                      RecoveryStrategy.INCREASE_TIMEOUT),
    # Permission problems
    ("PermissionError",              RecoveryStrategy.MANUAL_INTERVENTION),
    ("permission denied",            RecoveryStrategy.MANUAL_INTERVENTION),
    ("Access is denied",             RecoveryStrategy.MANUAL_INTERVENTION),
    # Network / transient problems
    ("ConnectionRefusedError",       RecoveryStrategy.RETRY_WITH_FIXES),
    ("ConnectionResetError",         RecoveryStrategy.RETRY_WITH_FIXES),
    ("OSError",                      RecoveryStrategy.RETRY_WITH_FIXES),
    # Test assertion failures — code is wrong, regenerate
    ("AssertionError",               RecoveryStrategy.LLM_REGEN),
    ("FAILED",                       RecoveryStrategy.LLM_REGEN),
    ("assert",                       RecoveryStrategy.LLM_REGEN),
]

# (pattern, strategy) — applied against failure_reason
_FAILURE_REASON_RULES: List[Tuple[str, RecoveryStrategy]] = [
    ("missing_python_dependency",    RecoveryStrategy.INSTALL_DEPS),
    ("syntax_error",                 RecoveryStrategy.LLM_REGEN),
    ("llm_failure",                  RecoveryStrategy.LLM_REGEN),
    ("test_failure",                 RecoveryStrategy.LLM_REGEN),
    ("assertion_failure",            RecoveryStrategy.LLM_REGEN),
    ("execution_timeout",            RecoveryStrategy.INCREASE_TIMEOUT),
    ("permission_error",             RecoveryStrategy.MANUAL_INTERVENTION),
    ("network_error",                RecoveryStrategy.RETRY_WITH_FIXES),
    ("epistemic_contradiction",      RecoveryStrategy.LLM_REGEN),
    ("low_confidence",               RecoveryStrategy.REDUCE_SCOPE),
    ("docker_error",                 RecoveryStrategy.SANDBOX_RETRY),
    ("unhandled_exception",          RecoveryStrategy.LLM_REGEN),
]

# Confidence thresholds
_LOW_CONFIDENCE_THRESHOLD  = 0.30
_VERY_LOW_CONFIDENCE_THRESHOLD = 0.15


def _already_tried(ctx: RetryContext, strategy: RecoveryStrategy) -> bool:
    """Return True if this strategy was already attempted (regardless of outcome)."""
    base = strategy.value
    return any(s.startswith(base) for s in ctx.prior_strategies)


def _match_rules(
    text: str,
    rules: List[Tuple[str, RecoveryStrategy]],
    ctx: RetryContext,
) -> RecoveryStrategy | None:
    text_lower = text.lower()
    for pattern, strategy in rules:
        if pattern.lower() in text_lower:
            if not _already_tried(ctx, strategy):
                return strategy
    return None


class StrategySelector:
    """
    Stateless selector that maps a RetryContext to a RecoveryStrategy.

    Selection priority
    ------------------
    1. Pattern intelligence  — use reflection history recommendation if available
    2. Stderr keyword match  — fast surface-level signal
    3. Failure reason match  — normalized taxonomy from pattern detection
    4. Epistemic contradiction detected
    5. Confidence threshold  — very low → reduce scope
    6. Attempt escalation    — many failed attempts → reduce scope / manual
    7. Fallback              — RETRY_WITH_FIXES, then NONE
    """

    def select(self, ctx: RetryContext) -> RecoveryStrategy:
        """Return the best RecoveryStrategy for *ctx*."""

        # 1. Pattern intelligence recommendation (from ReflectionEngine.recommend_recovery)
        if ctx.recommended_strategy:
            try:
                candidate = RecoveryStrategy(ctx.recommended_strategy)
                if not _already_tried(ctx, candidate):
                    return candidate
            except ValueError:
                pass  # unknown value — fall through

        # 2. Stderr keyword analysis
        combined_stderr = (ctx.stderr or "") + " " + (ctx.failure_reason or "")
        stderr_match = _match_rules(combined_stderr, _STDERR_RULES, ctx)
        if stderr_match:
            return stderr_match

        # 3. Failure reason taxonomy
        if ctx.failure_reason:
            reason_match = _match_rules(ctx.failure_reason, _FAILURE_REASON_RULES, ctx)
            if reason_match:
                return reason_match

        # 4. Epistemic contradictions → regenerate
        if ctx.contradictions and not _already_tried(ctx, RecoveryStrategy.LLM_REGEN):
            return RecoveryStrategy.LLM_REGEN

        # 5. Very low confidence → reduce scope
        if ctx.confidence < _VERY_LOW_CONFIDENCE_THRESHOLD:
            if not _already_tried(ctx, RecoveryStrategy.REDUCE_SCOPE):
                return RecoveryStrategy.REDUCE_SCOPE

        # 6. Low confidence → regen
        if ctx.confidence < _LOW_CONFIDENCE_THRESHOLD:
            if not _already_tried(ctx, RecoveryStrategy.LLM_REGEN):
                return RecoveryStrategy.LLM_REGEN

        # 7. Attempt escalation — many retries → escalate to manual
        if ctx.attempt_number >= 4 and not _already_tried(ctx, RecoveryStrategy.MANUAL_INTERVENTION):
            return RecoveryStrategy.MANUAL_INTERVENTION

        # 8. Generic retry fallback
        if not _already_tried(ctx, RecoveryStrategy.RETRY_WITH_FIXES):
            return RecoveryStrategy.RETRY_WITH_FIXES

        return RecoveryStrategy.NONE

    def explain(self, ctx: RetryContext, chosen: RecoveryStrategy) -> str:
        """Return a human-readable explanation of why this strategy was chosen."""
        reasons: list[str] = []

        if ctx.recommended_strategy == chosen.value:
            reasons.append(f"reflection history recommended '{chosen.value}'")
        if ctx.confidence < _LOW_CONFIDENCE_THRESHOLD:
            reasons.append(f"low confidence ({ctx.confidence:.2f})")
        if ctx.contradictions:
            reasons.append(f"{len(ctx.contradictions)} contradiction(s) detected")
        if ctx.attempt_number > 1:
            reasons.append(f"attempt #{ctx.attempt_number}")
        stderr_lower = (ctx.stderr or "").lower()
        for pattern, strategy in _STDERR_RULES:
            if strategy == chosen and pattern.lower() in stderr_lower:
                reasons.append(f"stderr contains '{pattern}'")
                break

        if not reasons:
            reasons.append("default selection")

        return f"Strategy '{chosen.value}' chosen because: {'; '.join(reasons)}."
