"""
Resource Models — data contracts for STEP 8 Resource-Aware Scheduling.

Defines the snapshot, threshold, and scheduling-decision types used throughout
the cognition.resources subsystem.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class PressureLevel(str, Enum):
    """Overall infrastructure pressure classification."""

    NONE     = "none"
    LOW      = "low"
    MODERATE = "moderate"
    HIGH     = "high"
    CRITICAL = "critical"


class ResourceSnapshot(BaseModel):
    """Point-in-time capture of host resource utilization."""

    cpu_percent:  float    = Field(0.0, ge=0.0, le=100.0, description="CPU utilization %")
    ram_percent:  float    = Field(0.0, ge=0.0, le=100.0, description="RAM utilization %")
    gpu_percent:  float    = Field(0.0, ge=0.0, le=100.0, description="GPU utilization %")
    disk_percent: float    = Field(0.0, ge=0.0, le=100.0, description="Primary disk utilization %")
    active_tasks: int      = Field(0,   ge=0,              description="Tasks currently executing")
    net_sent_mb:  float    = Field(0.0, ge=0.0,            description="MB sent since boot")
    net_recv_mb:  float    = Field(0.0, ge=0.0,            description="MB received since boot")
    timestamp:    datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of this reading",
    )

    def to_dict(self) -> dict:
        return {
            "cpu_percent":  self.cpu_percent,
            "ram_percent":  self.ram_percent,
            "gpu_percent":  self.gpu_percent,
            "disk_percent": self.disk_percent,
            "active_tasks": self.active_tasks,
            "net_sent_mb":  self.net_sent_mb,
            "net_recv_mb":  self.net_recv_mb,
            "timestamp":    self.timestamp.isoformat(),
        }


class ResourceThresholds(BaseModel):
    """
    Configurable per-metric warning/critical thresholds (%).

    Two levels per metric:
      ``warning``  — begin reducing parallelism
      ``critical`` — hard reduction; possibly postpone tasks
    """

    cpu_warning:   float = Field(70.0, ge=0.0, le=100.0)
    cpu_critical:  float = Field(90.0, ge=0.0, le=100.0)
    ram_warning:   float = Field(75.0, ge=0.0, le=100.0)
    ram_critical:  float = Field(88.0, ge=0.0, le=100.0)
    gpu_warning:   float = Field(75.0, ge=0.0, le=100.0)
    gpu_critical:  float = Field(90.0, ge=0.0, le=100.0)
    disk_warning:  float = Field(80.0, ge=0.0, le=100.0)
    disk_critical: float = Field(95.0, ge=0.0, le=100.0)


class SchedulingDecision(BaseModel):
    """
    Output of CapacityEngine.assess() — what the scheduler should do next.

    Consumed by TaskGraph.execute() to:
      - adjust the active semaphore (``recommended_parallelism``)
      - skip LLM-heavy nodes (``should_postpone_llm``)
      - emit resource_pressure events to the event bus (``actions``)
    """

    recommended_parallelism: int           = Field(1, ge=1)
    pressure_level:          PressureLevel  = PressureLevel.NONE
    pressure_sources:        List[str]      = Field(default_factory=list)
    should_postpone_llm:     bool           = False
    postpone_reason:         str            = ""
    actions:                 List[str]      = Field(default_factory=list)
    snapshot:                Optional[ResourceSnapshot] = None

    def to_dict(self) -> dict:
        return {
            "recommended_parallelism": self.recommended_parallelism,
            "pressure_level":          self.pressure_level.value,
            "pressure_sources":        self.pressure_sources,
            "should_postpone_llm":     self.should_postpone_llm,
            "postpone_reason":         self.postpone_reason,
            "actions":                 self.actions,
            "snapshot":                self.snapshot.to_dict() if self.snapshot else None,
        }
