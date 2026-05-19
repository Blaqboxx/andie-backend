"""
STEP 10A — Agent Registry
=========================
Authoritative in-memory registry of all agents in the multi-agent system.

Provides:
- register / deregister
- query by role, capability, availability
- best_agent_for_role — highest-confidence available agent for a given role
- status / confidence / heartbeat updates
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from .agent_models import AgentCapability, AgentRole, AgentState, AgentStatus


class AgentRegistry:
    """Authoritative registry of all known agents.

    Thread-safety: single-threaded asyncio use only.
    """

    def __init__(self) -> None:
        self._agents: Dict[str, AgentState] = {}

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def register(self, agent: AgentState) -> None:
        self._agents[agent.agent_id] = agent

    def deregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    def get(self, agent_id: str) -> Optional[AgentState]:
        return self._agents.get(agent_id)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def __len__(self) -> int:
        return len(self._agents)

    # ── Queries ───────────────────────────────────────────────────────────────

    def all_agents(self) -> List[AgentState]:
        return list(self._agents.values())

    def available_agents(self) -> List[AgentState]:
        return [a for a in self._agents.values() if a.is_available()]

    def agents_with_role(self, role: AgentRole) -> List[AgentState]:
        return [a for a in self._agents.values() if a.role == role]

    def agents_with_capability(self, cap: AgentCapability) -> List[AgentState]:
        return [a for a in self._agents.values() if a.has_capability(cap)]

    def best_agent_for_role(
        self,
        role: AgentRole,
        exclude: Optional[List[str]] = None,
    ) -> Optional[AgentState]:
        """Return highest-confidence available agent with the given role."""
        excluded = set(exclude or [])
        candidates = [
            a for a in self._agents.values()
            if a.role == role and a.is_available() and a.agent_id not in excluded
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.confidence)

    def best_agent_for_capability(
        self,
        cap: AgentCapability,
        exclude: Optional[List[str]] = None,
    ) -> Optional[AgentState]:
        """Return highest-confidence available agent that has the capability."""
        excluded = set(exclude or [])
        candidates = [
            a for a in self._agents.values()
            if a.has_capability(cap) and a.is_available() and a.agent_id not in excluded
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.confidence)

    # ── Mutations ─────────────────────────────────────────────────────────────

    def update_status(self, agent_id: str, status: AgentStatus) -> None:
        agent = self._agents.get(agent_id)
        if agent:
            agent.status = status

    def update_confidence(self, agent_id: str, confidence: float) -> None:
        agent = self._agents.get(agent_id)
        if agent:
            agent.confidence = max(0.0, min(1.0, confidence))

    def heartbeat(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if agent:
            agent.last_heartbeat = datetime.now(timezone.utc)
            if agent.status == AgentStatus.OFFLINE:
                agent.status = AgentStatus.IDLE

    def snapshot(self) -> List[dict]:
        return [a.to_dict() for a in self._agents.values()]
