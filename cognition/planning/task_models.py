"""
Task Models — data contracts for ANDIE's goal decomposition and task graph subsystem.

A goal is decomposed into a directed acyclic graph (DAG) of TaskNodes.
Each node is independently validated, recovered, and tracked.  The graph
captures causal dependency chains so failures are localized, not catastrophic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    PENDING   = "pending"    # not yet started
    READY     = "ready"      # all dependencies satisfied, can execute
    RUNNING   = "running"    # currently executing
    SUCCESS   = "success"    # completed successfully
    FAILED    = "failed"     # execution failed (may retry)
    BLOCKED   = "blocked"    # a dependency failed — cannot proceed
    SKIPPED   = "skipped"    # explicitly skipped (e.g., by reduce_scope)
    RETRYING  = "retrying"   # failed once, recovery in progress


class TaskPriority(str, Enum):
    CRITICAL = "critical"    # must succeed; failure blocks everything downstream
    HIGH     = "high"
    MEDIUM   = "medium"
    LOW      = "low"         # nice-to-have; skip if graph is struggling


# ---------------------------------------------------------------------------
# TaskNode
# ---------------------------------------------------------------------------


class TaskNode(BaseModel):
    """
    A single unit of work within a goal's execution graph.

    Each node is:
      - independently executable
      - epistemically validated on completion
      - retryable with adaptive strategy
      - capable of propagating failure to dependents
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:8],
        description="Short unique identifier (auto-generated or planner-assigned).",
    )
    description: str = Field(..., description="Natural-language description of this task.")
    dependencies: List[str] = Field(
        default_factory=list,
        description="IDs of TaskNodes that must succeed before this node can run.",
    )

    # ── Status tracking ────────────────────────────────────────────────
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int   = Field(0, ge=0)
    max_retries: int   = Field(3, ge=0)
    priority: TaskPriority = TaskPriority.MEDIUM

    # ── Execution signals ──────────────────────────────────────────────
    run_command: Optional[str] = Field(
        None, description="Shell command to execute this task, if known at planning time."
    )
    files: List[str] = Field(
        default_factory=list,
        description="Files this task is expected to produce or modify.",
    )
    timeout_seconds: int = Field(60, ge=1)

    # ── Epistemic signals (populated after execution) ──────────────────
    confidence: float    = Field(0.0, ge=0.0, le=1.0)
    exit_code: Optional[int] = None
    stdout: str          = ""
    stderr: str          = ""
    failure_reason: Optional[str] = None
    recovery_strategy: Optional[str] = None
    epistemic_status: Optional[str] = None

    # ── Metadata ───────────────────────────────────────────────────────
    tags: List[str]          = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime     = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # ── Computed helpers ───────────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        """True if the node has reached a final state (no further transitions)."""
        return self.status in (
            TaskStatus.SUCCESS,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.SKIPPED,
        )

    @property
    def can_retry(self) -> bool:
        return self.status == TaskStatus.FAILED and self.retry_count < self.max_retries

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def mark_running(self) -> None:
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now(timezone.utc)

    def mark_success(self, confidence: float = 1.0, stdout: str = "", stderr: str = "") -> None:
        self.status = TaskStatus.SUCCESS
        self.confidence = confidence
        self.stdout = stdout
        self.stderr = stderr
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(
        self,
        reason: str,
        exit_code: int = 1,
        stdout: str = "",
        stderr: str = "",
        confidence: float = 0.0,
    ) -> None:
        self.status = TaskStatus.FAILED
        self.failure_reason = reason
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.confidence = confidence
        self.completed_at = datetime.now(timezone.utc)

    def mark_blocked(self, because_of: str) -> None:
        self.status = TaskStatus.BLOCKED
        self.failure_reason = f"Blocked by failed dependency: {because_of}"
        self.completed_at = datetime.now(timezone.utc)

    def to_summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description[:80],
            "status": self.status.value,
            "confidence": round(self.confidence, 3),
            "retry_count": self.retry_count,
            "dependencies": self.dependencies,
            "failure_reason": self.failure_reason,
            "recovery_strategy": self.recovery_strategy,
            "duration_seconds": self.duration_seconds,
        }


# ---------------------------------------------------------------------------
# PlanResult — full outcome of a goal execution
# ---------------------------------------------------------------------------


class PlanResult(BaseModel):
    """Summary of the entire goal execution across all nodes."""

    goal: str
    total_nodes: int
    succeeded: int
    failed: int
    blocked: int
    skipped: int
    overall_confidence: float
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    node_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    critical_failures: List[str] = Field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_nodes == 0:
            return 0.0
        return self.succeeded / self.total_nodes

    @property
    def fully_successful(self) -> bool:
        return self.failed == 0 and self.blocked == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "total_nodes": self.total_nodes,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "blocked": self.blocked,
            "skipped": self.skipped,
            "success_rate": round(self.success_rate, 3),
            "overall_confidence": round(self.overall_confidence, 3),
            "fully_successful": self.fully_successful,
            "critical_failures": self.critical_failures,
            "node_summaries": self.node_summaries,
            "completed_at": self.completed_at.isoformat(),
        }
