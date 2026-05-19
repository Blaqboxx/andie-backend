"""
STEP 12A — Prediction Data Models
===================================
Pydantic v2 contracts shared across the entire prediction layer.

Model hierarchy
---------------
Risk layer:
    RiskFactor         — a single contributing risk signal with weight + confidence
    RiskAssessment     — complete risk profile for a task before execution

Infrastructure forecast layer:
    PressureLevel      — low / moderate / high / critical
    InfrastructureForecast — predicted pressure/failure probability for a node

Simulation layer:
    SimulationPath     — one candidate execution path (e.g. "as-is", "adapted")
    SimulationResult   — comparison of all paths with recommended action

Trajectory layer:
    TrendDirection     — improving / stable / declining / volatile
    DataPoint          — (timestamp, value) pair for a time series
    TrajectoryReport   — trend analysis for a task, node, or agent
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import auto
from typing import Any, Dict, List, Optional

try:
    from pydantic import BaseModel, Field, field_validator
except ImportError:
    from pydantic import BaseModel, Field, validator as field_validator  # type: ignore

from enum import Enum


# ── Enumerations ──────────────────────────────────────────────────────────────

class PressureLevel(str, Enum):
    LOW      = "low"
    MODERATE = "moderate"
    HIGH     = "high"
    CRITICAL = "critical"

    @classmethod
    def from_probability(cls, p: float) -> "PressureLevel":
        if p < 0.25:   return cls.LOW
        if p < 0.50:   return cls.MODERATE
        if p < 0.75:   return cls.HIGH
        return cls.CRITICAL


class TrendDirection(str, Enum):
    IMPROVING = "improving"
    STABLE    = "stable"
    DECLINING = "declining"
    VOLATILE  = "volatile"
    UNKNOWN   = "unknown"    # insufficient data


class SimulationPathType(str, Enum):
    AS_IS    = "as_is"       # execute without changes
    ADAPTED  = "adapted"     # execute with preemptive adaptation
    DEFERRED = "deferred"    # delay until conditions improve
    ABORTED  = "aborted"     # do not execute; escalate


# ── Risk layer ────────────────────────────────────────────────────────────────

class RiskFactor(BaseModel):
    """A single, named signal contributing to the overall risk score."""
    name:        str   = Field(..., description="Short identifier for this signal")
    probability: float = Field(..., ge=0.0, le=1.0,
                               description="Contribution to failure probability (0-1)")
    weight:      float = Field(default=1.0, ge=0.0,
                               description="How much this signal is weighted")
    confidence:  float = Field(default=1.0, ge=0.0, le=1.0,
                               description="How reliable is this signal")
    source:      str   = Field(default="unknown",
                               description="Which subsystem generated this factor")
    detail:      str   = Field(default="")

    def weighted_probability(self) -> float:
        """Effective probability contribution: p * weight * confidence."""
        return round(self.probability * self.weight * self.confidence, 4)


class RiskAssessment(BaseModel):
    """Complete pre-execution risk profile for a task."""
    task_id:    str
    task:       str
    context_tags: List[str] = Field(default_factory=list)
    node_id:    Optional[str] = None
    agent_id:   Optional[str] = None

    # Core outputs
    predicted_failure_probability: float = Field(..., ge=0.0, le=1.0)
    confidence:                    float = Field(..., ge=0.0, le=1.0)
    pressure_level:                PressureLevel

    # Detail
    risk_factors:                  List[RiskFactor] = Field(default_factory=list)
    likely_failure_modes:          List[str]        = Field(default_factory=list)
    recommended_preemptive_actions: List[str]       = Field(default_factory=list)

    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def is_high_risk(self, threshold: float = 0.7) -> bool:
        return self.predicted_failure_probability >= threshold

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id":   self.task_id,
            "task":      self.task,
            "context_tags": self.context_tags,
            "node_id":   self.node_id,
            "predicted_failure_probability": self.predicted_failure_probability,
            "confidence": self.confidence,
            "pressure_level": self.pressure_level.value,
            "likely_failure_modes": self.likely_failure_modes,
            "recommended_preemptive_actions": self.recommended_preemptive_actions,
            "risk_factors": [{"name": f.name, "p": f.probability, "w": f.weight} for f in self.risk_factors],
            "timestamp": self.timestamp,
        }


# ── Infrastructure forecast layer ─────────────────────────────────────────────

class InfrastructureForecast(BaseModel):
    """Predicted pressure/failure probability on a specific node."""
    node_id:          str
    task:             str
    context_tags:     List[str] = Field(default_factory=list)

    failure_probability: float = Field(..., ge=0.0, le=1.0)
    pressure_level:      PressureLevel
    confidence:          float = Field(..., ge=0.0, le=1.0)

    # Contributing signals
    historical_reliability:  float = Field(..., ge=0.0, le=1.0,
                                           description="Node success rate from episodic memory")
    pattern_risk:            float = Field(default=0.0, ge=0.0, le=1.0,
                                           description="Risk score from semantic patterns")
    recent_failure_rate:     float = Field(default=0.0, ge=0.0, le=1.0,
                                           description="Failure rate in the last N episodes on this node")

    contributing_patterns:   List[str] = Field(default_factory=list)
    recommended_actions:     List[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id":  self.node_id,
            "task":     self.task,
            "failure_probability": self.failure_probability,
            "pressure_level": self.pressure_level.value,
            "confidence": self.confidence,
            "historical_reliability": self.historical_reliability,
            "contributing_patterns": self.contributing_patterns,
            "recommended_actions": self.recommended_actions,
        }


# ── Simulation layer ──────────────────────────────────────────────────────────

class SimulationPath(BaseModel):
    """One candidate execution path evaluated by the simulation engine."""
    path_type:            SimulationPathType
    success_probability:  float = Field(..., ge=0.0, le=1.0)
    expected_confidence:  float = Field(..., ge=0.0, le=1.0)
    adaptations:          List[str] = Field(default_factory=list,
                                            description="Actions to take before executing this path")
    cost:                 float = Field(default=0.0, ge=0.0,
                                        description="Relative cost/delay of this path (0=free, 1=max)")
    notes:                str   = Field(default="")

    def utility(self) -> float:
        """Score = success_probability * confidence * (1 - cost)."""
        return round(self.success_probability * self.expected_confidence * (1.0 - self.cost), 4)


class SimulationResult(BaseModel):
    """Full simulation output: paths evaluated + recommended action."""
    task_id:    str
    task:       str
    node_id:    Optional[str] = None

    risk_assessment:    RiskAssessment
    infra_forecast:     Optional[InfrastructureForecast] = None

    paths:              List[SimulationPath]
    recommended_path:   SimulationPathType
    recommended_adaptations: List[str] = Field(default_factory=list)

    overall_confidence: float = Field(..., ge=0.0, le=1.0)
    rationale:          str   = Field(default="")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def should_execute(self) -> bool:
        return self.recommended_path in (SimulationPathType.AS_IS, SimulationPathType.ADAPTED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id":   self.task_id,
            "task":      self.task,
            "node_id":   self.node_id,
            "recommended_path":    self.recommended_path.value,
            "recommended_adaptations": self.recommended_adaptations,
            "overall_confidence":  self.overall_confidence,
            "rationale":           self.rationale,
            "risk": self.risk_assessment.to_dict(),
            "infra": self.infra_forecast.to_dict() if self.infra_forecast else None,
            "paths": [
                {"type": p.path_type.value, "success_p": p.success_probability,
                 "utility": p.utility(), "adaptations": p.adaptations}
                for p in self.paths
            ],
            "timestamp": self.timestamp,
        }


# ── Trajectory layer ──────────────────────────────────────────────────────────

class DataPoint(BaseModel):
    """Single observation in a time series (e.g. confidence at a timestamp)."""
    timestamp: str
    value:     float

    def __lt__(self, other: "DataPoint") -> bool:
        return self.timestamp < other.timestamp


class TrajectoryReport(BaseModel):
    """Trend analysis for a task, node, or agent over time."""
    subject_type: str   = Field(..., description="'task' | 'node' | 'agent'")
    subject_id:   str

    series:       List[DataPoint] = Field(default_factory=list,
                                          description="Chronologically ordered observations")
    direction:    TrendDirection
    slope:        float = Field(default=0.0,
                                description="Normalised trend slope (-1 to +1)")
    volatility:   float = Field(default=0.0, ge=0.0,
                                description="Standard deviation of values in the series")

    first_half_mean: float = Field(default=0.0)
    second_half_mean: float = Field(default=0.0)
    overall_mean:    float = Field(default=0.0)
    sample_count:    int   = Field(default=0)

    alert:           Optional[str] = None   # human-readable alert if DECLINING or VOLATILE
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject_type":    self.subject_type,
            "subject_id":      self.subject_id,
            "direction":       self.direction.value,
            "slope":           self.slope,
            "volatility":      self.volatility,
            "first_half_mean": self.first_half_mean,
            "second_half_mean": self.second_half_mean,
            "overall_mean":    self.overall_mean,
            "sample_count":    self.sample_count,
            "alert":           self.alert,
        }
