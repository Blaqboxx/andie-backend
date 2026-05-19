"""
Node Models — data contracts for STEP 9 Distributed Node Orchestration.

Defines the full type vocabulary for ANDIE's multi-node cognition layer:

  NodeCapability  — what a node can do (GPU inference, Docker, Ollama, …)
  NodeRole        — broad role classification (ai_server, edge, worker, …)
  NodeStatus      — liveness state of a node
  NodeState       — complete snapshot of a node's identity + utilization
  NodeHealthReport— outcome of a single health-check poll
  RoutingDecision — output of NodeScheduler.route(), including rationale
  RemoteResult    — standardised result dict from remote / local execution
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Capability vocabulary ────────────────────────────────────────────────

class NodeCapability(str, Enum):
    """Discrete capabilities a node may advertise."""

    GPU_INFERENCE  = "gpu_inference"
    LLM_SERVING    = "llm_serving"
    OLLAMA         = "ollama"
    DOCKER         = "docker"
    BUILD          = "build"
    DATABASE       = "database"
    MONITORING     = "monitoring"
    TELEMETRY      = "telemetry"
    STORAGE        = "storage"
    NETWORKING     = "networking"
    LOW_POWER      = "low_power"
    GENERAL        = "general"


class NodeRole(str, Enum):
    AI_SERVER = "ai_server"
    EDGE      = "edge"
    WORKER    = "worker"
    GATEWAY   = "gateway"
    LOCAL     = "local"


class NodeStatus(str, Enum):
    ONLINE   = "online"
    DEGRADED = "degraded"   # reachable but under pressure
    OFFLINE  = "offline"    # unreachable
    UNKNOWN  = "unknown"    # never successfully polled


# ── Core node model ──────────────────────────────────────────────────────

class NodeState(BaseModel):
    """
    Complete identity + utilization snapshot for a single cluster node.

    Both the registry (static config) and the health monitor (live data)
    write to this model.  Static fields (hostname, capabilities, role, tags)
    are set once at registration; dynamic fields (cpu/ram/gpu/disk, status,
    last_seen) are refreshed by HealthMonitor.
    """

    node_id:      str                  = Field(..., description="Unique stable identifier")
    hostname:     str                  = Field(..., description="DNS name or IP")
    port:         int                  = Field(22,  description="SSH port for remote execution")
    ssh_user:     str                  = Field("root", description="SSH username")
    role:         NodeRole             = NodeRole.WORKER
    capabilities: List[NodeCapability] = Field(default_factory=list)
    tags:         List[str]            = Field(default_factory=list)

    # ── Live metrics (updated by HealthMonitor) ──────────────────────
    cpu_percent:  float    = Field(0.0, ge=0.0, le=100.0)
    ram_percent:  float    = Field(0.0, ge=0.0, le=100.0)
    gpu_percent:  float    = Field(0.0, ge=0.0, le=100.0)
    disk_percent: float    = Field(0.0, ge=0.0, le=100.0)
    active_tasks: int      = Field(0,   ge=0)

    # ── Liveness ─────────────────────────────────────────────────────
    status:    NodeStatus = NodeStatus.UNKNOWN
    last_seen: Optional[datetime] = None

    # ── Computed load score ──────────────────────────────────────────
    @property
    def load_score(self) -> float:
        """
        Composite load 0–100.  Weights: CPU 40%, RAM 40%, GPU 20%.
        Lower is better (less loaded).
        """
        return round(
            self.cpu_percent * 0.40
            + self.ram_percent * 0.40
            + self.gpu_percent * 0.20,
            2,
        )

    @property
    def is_available(self) -> bool:
        return self.status in (NodeStatus.ONLINE, NodeStatus.DEGRADED)

    def has_capability(self, cap: NodeCapability) -> bool:
        return cap in self.capabilities

    def update_metrics(
        self,
        *,
        cpu: float,
        ram: float,
        gpu: float = 0.0,
        disk: float = 0.0,
        status: NodeStatus = NodeStatus.ONLINE,
        active_tasks: int = 0,
    ) -> None:
        """In-place metric refresh called by HealthMonitor."""
        self.cpu_percent  = cpu
        self.ram_percent  = ram
        self.gpu_percent  = gpu
        self.disk_percent = disk
        self.status       = status
        self.active_tasks = active_tasks
        self.last_seen    = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id":      self.node_id,
            "hostname":     self.hostname,
            "role":         self.role.value,
            "capabilities": [c.value for c in self.capabilities],
            "status":       self.status.value,
            "cpu_percent":  self.cpu_percent,
            "ram_percent":  self.ram_percent,
            "gpu_percent":  self.gpu_percent,
            "disk_percent": self.disk_percent,
            "active_tasks": self.active_tasks,
            "load_score":   self.load_score,
            "last_seen":    self.last_seen.isoformat() if self.last_seen else None,
        }


# ── Health report ────────────────────────────────────────────────────────

class NodeHealthReport(BaseModel):
    """Result of a single health-check poll sent to HealthMonitor."""

    node_id:    str
    reachable:  bool
    cpu:        float = 0.0
    ram:        float = 0.0
    gpu:        float = 0.0
    disk:       float = 0.0
    latency_ms: float = 0.0
    error:      str   = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Routing decision ─────────────────────────────────────────────────────

class RoutingDecision(BaseModel):
    """
    Output of NodeScheduler.route().

    Captures which node was selected for a task and the full rationale
    (capability match, load comparison, fallback flags).
    """

    task_id:           str
    selected_node_id:  str
    selected_hostname: str
    reason:            str
    is_local:          bool  = False
    is_fallback:       bool  = False
    candidates_seen:   int   = 0
    load_score:        float = 0.0
    required_caps:     List[str] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id":           self.task_id,
            "selected_node_id":  self.selected_node_id,
            "selected_hostname": self.selected_hostname,
            "reason":            self.reason,
            "is_local":          self.is_local,
            "is_fallback":       self.is_fallback,
            "candidates_seen":   self.candidates_seen,
            "load_score":        self.load_score,
            "required_caps":     self.required_caps,
        }


# ── Remote result ────────────────────────────────────────────────────────

class RemoteResult(BaseModel):
    """
    Standardised execution result for both local and remote task runs.

    Always compatible with the ``executor(node) -> dict`` contract
    expected by TaskGraph.
    """

    exit_code:   int   = -1
    stdout:      str   = ""
    stderr:      str   = ""
    status:      str   = "error"
    node_id:     str   = "local"
    hostname:    str   = "localhost"
    latency_ms:  float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exit_code":  self.exit_code,
            "stdout":     self.stdout,
            "stderr":     self.stderr,
            "status":     self.status,
            "node_id":    self.node_id,
            "hostname":   self.hostname,
            "latency_ms": self.latency_ms,
        }
