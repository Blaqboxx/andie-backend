"""
STEP 10A — Agent Router
=======================
Routes task delegation and messages to the most appropriate agent.

Routing priority
----------------
1. ``agent:<agent_id>`` tag  → direct pin (always honoured if agent available)
2. ``role:<role>``   tag     → best available agent with that role
3. Default fallback          → highest-confidence EXECUTOR

The router also handles delegation: it calls AgentRouter.delegate() which
both selects the agent AND dispatches a DELEGATE_TASK message via MessageBus.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .agent_models import AgentMessageTopic, AgentRole, AgentState
from .agent_registry import AgentRegistry
from .message_bus import MessageBus

logger = logging.getLogger(__name__)


class AgentRoutingDecision:
    """Result of an agent routing decision."""

    def __init__(
        self,
        task_id: str,
        agent: Optional[AgentState],
        reason: str,
        is_fallback: bool = False,
    ) -> None:
        self.task_id          = task_id
        self.agent            = agent
        self.reason           = reason
        self.is_fallback      = is_fallback
        self.selected_agent_id = agent.agent_id if agent else None
        self.selected_role     = agent.role      if agent else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id":          self.task_id,
            "selected_agent_id": self.selected_agent_id,
            "selected_role":    self.selected_role.value if self.selected_role else None,
            "reason":           self.reason,
            "is_fallback":      self.is_fallback,
        }


class AgentRouter:
    """Routes task delegation to the best available agent.

    Routing is stateless — every call re-evaluates the registry.
    """

    def __init__(self, registry: AgentRegistry, bus: MessageBus) -> None:
        self._registry = registry
        self._bus      = bus

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(
        self,
        task_id: str,
        tags: List[str],
        exclude: Optional[List[str]] = None,
    ) -> AgentRoutingDecision:
        """Select the best agent for this task based on its tags."""
        excluded = list(exclude or [])

        # 1. Direct pin: agent:<agent_id>
        for tag in tags:
            if tag.startswith("agent:"):
                target_id = tag[len("agent:"):]
                agent = self._registry.get(target_id)
                if agent and agent.is_available() and target_id not in excluded:
                    return AgentRoutingDecision(
                        task_id, agent,
                        f"Pinned to agent '{target_id}'",
                        is_fallback=False,
                    )
                logger.warning("Pinned agent '%s' not available — falling back", target_id)

        # 2. Role-based routing: role:<role>
        for tag in tags:
            if tag.startswith("role:"):
                role_str = tag[len("role:"):]
                try:
                    role = AgentRole(role_str)
                    agent = self._registry.best_agent_for_role(role, exclude=excluded)
                    if agent:
                        return AgentRoutingDecision(
                            task_id, agent,
                            f"Role '{role_str}' routing",
                            is_fallback=False,
                        )
                    logger.warning("No available agent for role '%s'", role_str)
                except ValueError:
                    logger.warning("Unknown role tag '%s'", role_str)

        # 3. Fallback: best EXECUTOR
        agent = self._registry.best_agent_for_role(AgentRole.EXECUTOR, exclude=excluded)
        if agent:
            return AgentRoutingDecision(
                task_id, agent,
                "Default executor fallback",
                is_fallback=True,
            )

        # 4. Nothing available
        return AgentRoutingDecision(
            task_id, None,
            "No available agents in registry",
            is_fallback=True,
        )

    # ── Delegation ────────────────────────────────────────────────────────────

    async def delegate(
        self,
        task_id: str,
        tags: List[str],
        payload: Dict[str, Any],
        from_agent: str = "coordinator",
        exclude: Optional[List[str]] = None,
    ) -> AgentRoutingDecision:
        """Route the task and send a DELEGATE_TASK message to the selected agent."""
        decision = self.route(task_id, tags, exclude=exclude)
        if decision.selected_agent_id:
            await self._bus.send(
                from_agent=from_agent,
                to_agent=decision.selected_agent_id,
                topic=AgentMessageTopic.DELEGATE_TASK,
                payload={"task_id": task_id, "tags": tags, **payload},
            )
        else:
            logger.error("delegate(): no agent available for task '%s'", task_id)
        return decision

    # ── Explain ───────────────────────────────────────────────────────────────

    def explain(self, task_id: str, tags: List[str]) -> str:
        d = self.route(task_id, tags)
        if d.selected_agent_id:
            return (
                f"Task '{task_id}' → agent '{d.selected_agent_id}' "
                f"[{d.selected_role.value}] | {d.reason} | fallback={d.is_fallback}"
            )
        return f"Task '{task_id}' → NO AGENT AVAILABLE | {d.reason}"
