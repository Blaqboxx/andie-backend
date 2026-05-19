"""
cognition.agents — STEP 10 Multi-Agent Cognitive Coordination
=============================================================
Public API for the multi-agent coordination layer.

Modules
-------
agent_models     — AgentState, AgentMessage, consensus data contracts
agent_registry   — In-memory registry of all active agents
message_bus      — Async pub/sub inter-agent message bus
agent_router     — Task-to-agent routing (pin / role / capability / fallback)
consensus_engine — Multi-agent consensus validation

Quick start
-----------
    from cognition.agents import (
        AgentRole, AgentCapability, AgentState, AgentStatus,
        AgentRegistry, MessageBus, AgentRouter, ConsensusEngine,
    )

    registry = AgentRegistry()
    bus      = MessageBus()
    router   = AgentRouter(registry, bus)
    engine   = ConsensusEngine(registry, bus)
"""

from .agent_models import (
    AgentCapability,
    AgentMessage,
    AgentMessageTopic,
    AgentRole,
    AgentState,
    AgentStatus,
    ConsensusRequest,
    ConsensusResult,
    ConsensusVote,
)
from .agent_registry  import AgentRegistry
from .agent_router    import AgentRouter, AgentRoutingDecision
from .consensus_engine import ConsensusEngine
from .message_bus     import MessageBus

__all__ = [
    # models
    "AgentRole",
    "AgentStatus",
    "AgentCapability",
    "AgentMessageTopic",
    "AgentState",
    "AgentMessage",
    "ConsensusRequest",
    "ConsensusVote",
    "ConsensusResult",
    # registry
    "AgentRegistry",
    # bus
    "MessageBus",
    # router
    "AgentRouter",
    "AgentRoutingDecision",
    # consensus
    "ConsensusEngine",
]
