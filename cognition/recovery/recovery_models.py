"""
Recovery Models — data contracts for ANDIE's adaptive retry subsystem.

These models represent the full lifecycle of a recovery attempt:
  - the strategy chosen
  - the context that informed the choice
  - the outcome of the retry
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Strategy enumeration
# ---------------------------------------------------------------------------


class RecoveryStrategy(str, Enum):
    """Available recovery strategies, ordered from cheapest to most invasive."""

    NONE               = "none"
    RETRY_WITH_FIXES   = "retry_with_fixes"    # re-run with same code, minor tweak
    INSTALL_DEPS       = "install_deps"         # pip install missing packages first
    LLM_REGEN          = "llm_regen"            # ask LLM to regenerate the failing code
    REDUCE_SCOPE       = "reduce_scope"         # simplify the task / shrink the goal
    INCREASE_TIMEOUT   = "increase_timeout"     # give execution more time
    SANDBOX_RETRY      = "sandbox_retry"        # run in isolated sandbox environment
    ROLLBACK           = "rollback"             # revert to last known-good state
    MANUAL_INTERVENTION = "manual_intervention" # flag for human review


# ---------------------------------------------------------------------------
# Recovery context — what ANDIE knows before choosing a strategy
# ---------------------------------------------------------------------------


class RetryContext(BaseModel):
    """
    Full situational context passed to the retry engine and injected into
    any LLM-based regeneration prompt.

    Capturing all signals here prevents "blind" retries and enables
    context-aware regeneration.
    """

    task: str = Field(..., description="Original natural-language task brief.")
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # ── What went wrong ────────────────────────────────────────────────
    failure_reason: Optional[str] = Field(
        None, description="Human-readable failure summary."
    )
    exit_code: int = Field(0, description="Exit code from the failed execution.")
    stderr: str = Field("", description="Standard error output (truncated if long).")
    stdout: str = Field("", description="Standard output (truncated if long).")

    # ── Epistemic signals ──────────────────────────────────────────────
    confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="Epistemic confidence score of the failed run."
    )
    contradictions: List[str] = Field(
        default_factory=list,
        description="Contradiction descriptions detected during evaluation.",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-fatal warnings from epistemic evaluation.",
    )

    # ── History signals ────────────────────────────────────────────────
    attempt_number: int = Field(1, ge=1, description="Which retry attempt this is (1-indexed).")
    prior_strategies: List[str] = Field(
        default_factory=list,
        description="Strategies already attempted and their outcomes, e.g. ['install_deps:failed'].",
    )
    prior_failure_reasons: List[str] = Field(
        default_factory=list,
        description="Failure reasons from previous attempts.",
    )

    # ── Pattern intelligence ───────────────────────────────────────────
    pattern_label: Optional[str] = Field(
        None,
        description="Failure taxonomy label inferred from reflection pattern detection.",
    )
    recommended_strategy: Optional[str] = Field(
        None,
        description="Strategy recommended by ReflectionEngine.recommend_recovery().",
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def stderr_snippet(self) -> str:
        """First 500 chars of stderr for prompt injection."""
        return self.stderr[:500]

    @property
    def stdout_snippet(self) -> str:
        """First 300 chars of stdout for prompt injection."""
        return self.stdout[:300]

    def prior_summary(self) -> str:
        """Compact human-readable history string for prompt injection."""
        if not self.prior_strategies:
            return "No prior attempts."
        lines = []
        for i, s in enumerate(self.prior_strategies, 1):
            reason = self.prior_failure_reasons[i - 1] if i <= len(self.prior_failure_reasons) else "unknown"
            lines.append(f"  Attempt {i}: strategy={s}, failure={reason}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Retry result
# ---------------------------------------------------------------------------


class RetryResult(BaseModel):
    """
    The outcome of a single retry attempt.

    Persisted back into the reflection log so ANDIE learns which recovery
    strategies actually work.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    attempt_number: int
    strategy: RecoveryStrategy
    succeeded: bool
    new_exit_code: int
    new_stdout: str = ""
    new_stderr: str = ""
    new_confidence: float = 0.0
    notes: str = ""
    executed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "attempt_number": self.attempt_number,
            "strategy": self.strategy.value,
            "succeeded": self.succeeded,
            "new_exit_code": self.new_exit_code,
            "new_confidence": self.new_confidence,
            "notes": self.notes,
            "executed_at": self.executed_at.isoformat(),
        }
