"""
Reflection Models — data contracts for the cognition/reflection subsystem.

These Pydantic models capture what ANDIE experienced during a build or
task execution, forming the raw material for pattern detection and
self-improvement.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ReflectionOutcome(str, Enum):
    SUCCESS           = "success"
    PARTIAL_SUCCESS   = "partial_success"
    EPISTEMIC_FAILURE = "epistemic_failure"
    MAX_ITERATIONS    = "max_iterations_reached"
    EXECUTION_ERROR   = "execution_error"
    UNKNOWN           = "unknown"


class RecoveryStrategy(str, Enum):
    RETRY_WITH_FIXES     = "retry_with_fixes"
    INSTALL_DEPS         = "install_deps"
    LLM_REGEN            = "llm_regen"
    FALLBACK_PLAN        = "fallback_plan"
    MANUAL_INTERVENTION  = "manual_intervention"
    NONE                 = "none"


# ---------------------------------------------------------------------------
# Core reflection record
# ---------------------------------------------------------------------------


class ReflectionRecord(BaseModel):
    """
    A single episodic memory of a completed build or task execution.

    Every field is designed to feed pattern detection so ANDIE can learn
    what works, what fails, and why.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task: str = Field(..., description="Natural-language brief / task description.")
    agent_id: str = "andie"

    # ── Outcome ────────────────────────────────────────────────────────
    raw_status: str       = Field(..., description="Status string as claimed by the executor.")
    epistemic_status: str = Field(..., description="Epistemic verdict from EpistemicEngine.")
    outcome: ReflectionOutcome = ReflectionOutcome.UNKNOWN
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    validated: bool = False

    # ── Evidence ───────────────────────────────────────────────────────
    exit_code: int = -1
    iterations: int = 1
    contradictions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    # ── Causal analysis ────────────────────────────────────────────────
    failure_reason: Optional[str] = Field(
        default=None,
        description="Primary reason for failure, extracted from contradictions or stderr.",
    )
    recovery_strategy: RecoveryStrategy = RecoveryStrategy.NONE
    recovery_succeeded: Optional[bool] = None

    # ── Context ────────────────────────────────────────────────────────
    stderr_snippet: str = ""
    stdout_snippet: str = ""
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # ── Derived helpers ────────────────────────────────────────────────

    @property
    def succeeded(self) -> bool:
        return self.epistemic_status in ("success", "success_with_warnings")

    @property
    def has_contradictions(self) -> bool:
        return bool(self.contradictions)

    def to_log_entry(self) -> Dict[str, Any]:
        """Compact dict for JSON persistence."""
        return {
            "id":                self.id,
            "task":              self.task[:200],
            "agent_id":          self.agent_id,
            "raw_status":        self.raw_status,
            "epistemic_status":  self.epistemic_status,
            "outcome":           self.outcome.value,
            "confidence":        self.confidence,
            "validated":         self.validated,
            "exit_code":         self.exit_code,
            "iterations":        self.iterations,
            "contradictions":    self.contradictions,
            "warnings":          self.warnings,
            "failure_reason":    self.failure_reason,
            "recovery_strategy": self.recovery_strategy.value,
            "recovery_succeeded":self.recovery_succeeded,
            "stderr_snippet":    self.stderr_snippet[:300],
            "stdout_snippet":    self.stdout_snippet[:300],
            "tags":              self.tags,
            "timestamp":         self.timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# Pattern summary model (produced by patterns.py)
# ---------------------------------------------------------------------------


class PatternSummary(BaseModel):
    """
    Aggregated pattern extracted from a set of ReflectionRecords.

    Describes a recurring failure mode or success pattern.
    """

    pattern_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    label: str = Field(..., description="Human-readable pattern name.")
    occurrences: int = 1
    failure_rate: float = Field(ge=0.0, le=1.0)
    avg_confidence: float = Field(ge=0.0, le=1.0)
    avg_iterations: float = 1.0
    dominant_failure_reason: Optional[str] = None
    dominant_recovery: RecoveryStrategy = RecoveryStrategy.NONE
    recovery_success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    example_tasks: List[str] = Field(default_factory=list)
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)
    actionable_hint: str = ""
