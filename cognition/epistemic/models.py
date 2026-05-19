"""
Epistemic Models — shared data structures for the cognition/epistemic subsystem.

These Pydantic models define the canonical shape of beliefs, confidence
assessments, contradictions, and validation results used across engine.py,
confidence.py, contradictions.py, and the validators.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ConfidenceLevel(str, Enum):
    """Qualitative confidence bucket derived from a float score."""
    CERTAIN   = "certain"     # >= 0.90
    HIGH      = "high"        # >= 0.75
    MODERATE  = "moderate"    # >= 0.50
    LOW       = "low"         # >= 0.25
    UNKNOWN   = "unknown"     # <  0.25


class BeliefSource(str, Enum):
    """Where a belief originated."""
    LLM          = "llm"
    MEMORY       = "memory"
    SENSOR       = "sensor"
    RULE         = "rule"
    USER         = "user"
    INFERENCE    = "inference"
    EXTERNAL_API = "external_api"


class BeliefStatus(str, Enum):
    """Lifecycle state of a belief."""
    ACTIVE      = "active"
    RETRACTED   = "retracted"
    SUPERSEDED  = "superseded"
    PENDING     = "pending"


class ContradictionSeverity(str, Enum):
    """How serious a detected contradiction is."""
    CRITICAL  = "critical"   # completely incompatible beliefs
    MAJOR     = "major"      # significant conflict
    MINOR     = "minor"      # partial overlap / ambiguity
    TOLERABLE = "tolerable"  # within acceptable uncertainty margin


class ValidationOutcome(str, Enum):
    PASS    = "pass"
    FAIL    = "fail"
    WARNING = "warning"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Core belief model
# ---------------------------------------------------------------------------


class Belief(BaseModel):
    """A single discrete belief held by the agent."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim: str = Field(..., description="Natural-language statement of the belief.")
    source: BeliefSource = BeliefSource.INFERENCE
    status: BeliefStatus = BeliefStatus.ACTIVE
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Scalar confidence in [0, 1].",
    )
    confidence_level: ConfidenceLevel = ConfidenceLevel.MODERATE
    context: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    superseded_by: Optional[str] = Field(
        default=None,
        description="ID of the belief that replaced this one.",
    )

    @field_validator("confidence_level", mode="before")
    @classmethod
    def derive_confidence_level(cls, v: Any, info: Any) -> ConfidenceLevel:
        """Auto-derive confidence_level from the confidence float if not supplied."""
        score: float = (info.data or {}).get("confidence", 0.5)
        if score >= 0.90:
            return ConfidenceLevel.CERTAIN
        if score >= 0.75:
            return ConfidenceLevel.HIGH
        if score >= 0.50:
            return ConfidenceLevel.MODERATE
        if score >= 0.25:
            return ConfidenceLevel.LOW
        return ConfidenceLevel.UNKNOWN


# ---------------------------------------------------------------------------
# Confidence assessment
# ---------------------------------------------------------------------------


class ConfidenceFactors(BaseModel):
    """Individual factors that contribute to an overall confidence score."""
    source_reliability: float = Field(default=0.5, ge=0.0, le=1.0)
    recency: float           = Field(default=0.5, ge=0.0, le=1.0)
    corroboration: float     = Field(default=0.5, ge=0.0, le=1.0)
    internal_consistency: float = Field(default=0.5, ge=0.0, le=1.0)
    domain_specificity: float   = Field(default=0.5, ge=0.0, le=1.0)


class ConfidenceAssessment(BaseModel):
    """Result of evaluating confidence for a belief or claim."""
    belief_id: str
    score: float = Field(ge=0.0, le=1.0)
    level: ConfidenceLevel
    factors: ConfidenceFactors = Field(default_factory=ConfidenceFactors)
    rationale: str = ""
    assessed_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Contradiction models
# ---------------------------------------------------------------------------


class Contradiction(BaseModel):
    """A detected conflict between two beliefs."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    belief_a_id: str
    belief_b_id: str
    severity: ContradictionSeverity = ContradictionSeverity.MINOR
    description: str = ""
    resolution: Optional[str] = Field(
        default=None,
        description="How (if) the contradiction was resolved.",
    )
    resolved: bool = False
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None


class ContradictionReport(BaseModel):
    """Aggregated contradiction scan results for a set of beliefs."""
    belief_ids_scanned: List[str]
    contradictions: List[Contradiction] = Field(default_factory=list)
    total_contradictions: int = 0
    critical_count: int = 0
    scanned_at: datetime = Field(default_factory=datetime.utcnow)

    def model_post_init(self, __context: Any) -> None:
        self.total_contradictions = len(self.contradictions)
        self.critical_count = sum(
            1 for c in self.contradictions
            if c.severity == ContradictionSeverity.CRITICAL
        )


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------


class ValidationResult(BaseModel):
    """Output of any validator (goal, output, runtime, syntax)."""

    validator: str
    target_id: str
    outcome: ValidationOutcome = ValidationOutcome.PASS
    messages: List[str] = Field(default_factory=list)
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    validated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def passed(self) -> bool:
        return self.outcome == ValidationOutcome.PASS


# ---------------------------------------------------------------------------
# Epistemic state — top-level snapshot
# ---------------------------------------------------------------------------


class EpistemicState(BaseModel):
    """
    Complete epistemic snapshot for the agent at a point in time.

    Holds all active beliefs, recent confidence assessments, open
    contradictions, and the last validation sweep results.
    """

    agent_id: str = "andie"
    beliefs: List[Belief] = Field(default_factory=list)
    confidence_assessments: List[ConfidenceAssessment] = Field(default_factory=list)
    contradiction_report: Optional[ContradictionReport] = None
    validation_results: List[ValidationResult] = Field(default_factory=list)
    snapshot_at: datetime = Field(default_factory=datetime.utcnow)

    # ------------------------------------------------------------------ #
    # Convenience helpers                                                  #
    # ------------------------------------------------------------------ #

    def active_beliefs(self) -> List[Belief]:
        return [b for b in self.beliefs if b.status == BeliefStatus.ACTIVE]

    def belief_by_id(self, belief_id: str) -> Optional[Belief]:
        return next((b for b in self.beliefs if b.id == belief_id), None)

    def has_critical_contradictions(self) -> bool:
        if self.contradiction_report is None:
            return False
        return self.contradiction_report.critical_count > 0

    def overall_confidence(self) -> float:
        """Mean confidence across all active beliefs."""
        active = self.active_beliefs()
        if not active:
            return 0.0
        return sum(b.confidence for b in active) / len(active)

    def failed_validations(self) -> List[ValidationResult]:
        return [r for r in self.validation_results if not r.passed]
