"""
STEP 10A — Agent Models
=======================
Data contracts for the multi-agent cognitive coordination layer.

Classes
-------
AgentRole           — Role each agent plays in the system
AgentStatus         — Lifecycle state of an agent
AgentCapability     — What an agent can do
AgentMessageTopic   — Topics on the inter-agent message bus
AgentState          — Live state of a single agent
AgentMessage        — A message on the bus (point-to-point or broadcast)
ConsensusRequest    — A multi-agent vote request
ConsensusVote       — A single agent's vote
ConsensusResult     — Aggregated outcome of a consensus round
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enumerations ──────────────────────────────────────────────────────────────

class AgentRole(str, Enum):
    PLANNER        = "planner"       # Decomposes goals → task graphs
    EXECUTOR       = "executor"      # Runs tasks, invokes commands
    SENTINEL       = "sentinel"      # Safety / security validation
    REFLECTION     = "reflection"    # Pattern learning, memory analysis
    SCHEDULER      = "scheduler"     # Resource-aware capacity decisions
    INFRASTRUCTURE = "infrastructure"# Node registry, health management
    COORDINATOR    = "coordinator"   # Orchestrates all other agents


class AgentStatus(str, Enum):
    IDLE    = "idle"
    ACTIVE  = "active"
    BUSY    = "busy"
    OFFLINE = "offline"
    ERROR   = "error"


class AgentCapability(str, Enum):
    TASK_DECOMPOSITION   = "task_decomposition"
    TASK_EXECUTION       = "task_execution"
    SAFETY_VALIDATION    = "safety_validation"
    PATTERN_LEARNING     = "pattern_learning"
    RESOURCE_SCHEDULING  = "resource_scheduling"
    NODE_MANAGEMENT      = "node_management"
    CONSENSUS_VOTING     = "consensus_voting"
    LLM_REASONING        = "llm_reasoning"
    EPISTEMIC_VALIDATION = "epistemic_validation"
    RECOVERY_PLANNING    = "recovery_planning"


class AgentMessageTopic(str, Enum):
    DELEGATE_TASK       = "delegate_task"
    TASK_RESULT         = "task_result"
    SAFETY_CHECK        = "safety_check"
    CONSENSUS_REQUEST   = "consensus_request"
    CONSENSUS_VOTE      = "consensus_vote"
    HEARTBEAT           = "heartbeat"
    STATUS_UPDATE       = "status_update"
    ALERT               = "alert"
    REFLECTION_TRIGGER  = "reflection_trigger"
    RESOURCE_PRESSURE   = "resource_pressure"


# ── Core Models ───────────────────────────────────────────────────────────────

class AgentState(BaseModel):
    """Live state of a single agent in the multi-agent system."""

    agent_id:       str
    role:           AgentRole
    capabilities:   List[AgentCapability] = Field(default_factory=list)
    status:         AgentStatus = AgentStatus.IDLE
    confidence:     float = Field(default=1.0, ge=0.0, le=1.0)
    node_id:        Optional[str] = None          # infrastructure node it runs on
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata:       Dict[str, Any] = Field(default_factory=dict)

    def has_capability(self, cap: AgentCapability) -> bool:
        return cap in self.capabilities

    def is_available(self) -> bool:
        return self.status in (AgentStatus.IDLE, AgentStatus.ACTIVE)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id":       self.agent_id,
            "role":           self.role.value,
            "capabilities":   [c.value for c in self.capabilities],
            "status":         self.status.value,
            "confidence":     self.confidence,
            "node_id":        self.node_id,
            "last_heartbeat": self.last_heartbeat.isoformat(),
        }


class AgentMessage(BaseModel):
    """A message sent between agents on the MessageBus."""

    msg_id:     str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    from_agent: str
    to_agent:   Optional[str] = None          # None → broadcast to all
    topic:      AgentMessageTopic
    payload:    Dict[str, Any] = Field(default_factory=dict)
    timestamp:  datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    priority:   int = Field(default=5, ge=1, le=10)  # 1 = highest

    def is_broadcast(self) -> bool:
        return self.to_agent is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "msg_id":     self.msg_id,
            "from_agent": self.from_agent,
            "to_agent":   self.to_agent,
            "topic":      self.topic.value,
            "payload":    self.payload,
            "timestamp":  self.timestamp.isoformat(),
            "priority":   self.priority,
        }


# ── Consensus Models ──────────────────────────────────────────────────────────

class ConsensusRequest(BaseModel):
    """A request for multi-agent consensus on a decision."""

    request_id:              str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    topic:                   str                          # human-readable subject
    payload:                 Dict[str, Any] = Field(default_factory=dict)
    required_approval_ratio: float = Field(default=0.67, ge=0.0, le=1.0)
    timeout_seconds:         float = Field(default=5.0)
    participants:            List[str] = Field(default_factory=list)


class ConsensusVote(BaseModel):
    """A single agent's vote on a ConsensusRequest."""

    request_id: str
    agent_id:   str
    approved:   bool
    reason:     str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    timestamp:  datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConsensusResult(BaseModel):
    """Aggregated outcome of a consensus round."""

    request_id:          str
    topic:               str
    approved:            bool
    total_votes:         int
    yes_votes:           int
    no_votes:            int
    abstained:           int
    approval_ratio:      float
    final_confidence:    float
    dissenting_reasons:  List[str] = Field(default_factory=list)
    participants:        List[str] = Field(default_factory=list)
    timestamp:           datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id":         self.request_id,
            "topic":              self.topic,
            "approved":           self.approved,
            "total_votes":        self.total_votes,
            "yes_votes":          self.yes_votes,
            "no_votes":           self.no_votes,
            "abstained":          self.abstained,
            "approval_ratio":     round(self.approval_ratio, 3),
            "final_confidence":   round(self.final_confidence, 3),
            "dissenting_reasons": self.dissenting_reasons,
            "participants":       self.participants,
        }
