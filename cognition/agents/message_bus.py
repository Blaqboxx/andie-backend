"""
STEP 10A — Message Bus
======================
Async pub/sub inter-agent communication bus.

Features
--------
- Point-to-point:  send(from, to, topic, payload)
- Broadcast:       broadcast(from, topic, payload) → all agents except sender
- Subscriptions:   subscribe(agent_id, topic, handler) — async callback
- Wildcard:        subscribe_all(agent_id, handler)  — all topics
- Message history: recent(n), messages_for(agent_id)

Handlers are called concurrently via asyncio.gather.
Exceptions in handlers are logged, not re-raised.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from .agent_models import AgentMessage, AgentMessageTopic

logger = logging.getLogger(__name__)

# Async callable (AgentMessage) → None
Handler = Callable[[AgentMessage], Coroutine]


class MessageBus:
    """Async pub/sub message bus for inter-agent cognitive coordination.

    Usage
    -----
    bus = MessageBus()
    bus.register_agent("planner")
    bus.subscribe("planner", AgentMessageTopic.DELEGATE_TASK, my_async_handler)

    await bus.send("coordinator", "planner", AgentMessageTopic.DELEGATE_TASK, {...})
    await bus.broadcast("coordinator", AgentMessageTopic.STATUS_UPDATE, {...})
    """

    def __init__(self, history_limit: int = 500) -> None:
        # (agent_id, topic.value) → list of handlers
        self._handlers: Dict[Tuple[str, str], List[Handler]] = {}
        # agent_id → catch-all handlers (subscribed to all topics)
        self._wildcard: Dict[str, List[Handler]] = {}
        # ordered message history
        self._history: List[AgentMessage] = []
        self._history_limit = history_limit
        # registered agents (used to determine broadcast recipients)
        self._agents: set[str] = set()

    # ── Agent registration ─────────────────────────────────────────────────

    def register_agent(self, agent_id: str) -> None:
        """Register an agent so it receives broadcasts."""
        self._agents.add(agent_id)

    def unregister_agent(self, agent_id: str) -> None:
        self._agents.discard(agent_id)
        # Remove handler registrations
        self._handlers = {
            k: v for k, v in self._handlers.items() if k[0] != agent_id
        }
        self._wildcard.pop(agent_id, None)

    # ── Subscriptions ──────────────────────────────────────────────────────

    def subscribe(
        self,
        agent_id: str,
        topic: AgentMessageTopic,
        handler: Handler,
    ) -> None:
        """Subscribe agent_id to a specific topic with an async handler."""
        self._agents.add(agent_id)
        key = (agent_id, topic.value)
        self._handlers.setdefault(key, []).append(handler)

    def subscribe_all(self, agent_id: str, handler: Handler) -> None:
        """Subscribe agent_id to all topics (wildcard)."""
        self._agents.add(agent_id)
        self._wildcard.setdefault(agent_id, []).append(handler)

    # ── Publishing ────────────────────────────────────────────────────────

    async def publish(self, message: AgentMessage) -> None:
        """Dispatch a message to its target(s) and invoke matching handlers."""
        self._history.append(message)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]

        if message.is_broadcast():
            recipients = [a for a in self._agents if a != message.from_agent]
        else:
            if message.to_agent not in self._agents:
                logger.warning("Message to unknown agent '%s' dropped", message.to_agent)
                return
            recipients = [message.to_agent]

        coros: List[Coroutine] = []
        for agent_id in recipients:
            key = (agent_id, message.topic.value)
            for h in self._handlers.get(key, []):
                coros.append(h(message))
            for h in self._wildcard.get(agent_id, []):
                coros.append(h(message))

        if coros:
            results = await asyncio.gather(*coros, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("Handler raised: %s", r)

    async def send(
        self,
        from_agent: str,
        to_agent: str,
        topic: AgentMessageTopic,
        payload: Dict[str, Any],
        priority: int = 5,
    ) -> AgentMessage:
        """Send a point-to-point message."""
        msg = AgentMessage(
            from_agent=from_agent,
            to_agent=to_agent,
            topic=topic,
            payload=payload,
            priority=priority,
        )
        await self.publish(msg)
        return msg

    async def broadcast(
        self,
        from_agent: str,
        topic: AgentMessageTopic,
        payload: Dict[str, Any],
        priority: int = 5,
    ) -> AgentMessage:
        """Broadcast a message to all registered agents (except sender)."""
        msg = AgentMessage(
            from_agent=from_agent,
            to_agent=None,
            topic=topic,
            payload=payload,
            priority=priority,
        )
        await self.publish(msg)
        return msg

    # ── History ───────────────────────────────────────────────────────────

    def recent(self, n: int = 20) -> List[AgentMessage]:
        """Return the n most recent messages."""
        return self._history[-n:]

    def messages_for(self, agent_id: str, n: int = 50) -> List[AgentMessage]:
        """Return messages addressed to agent_id or broadcast."""
        return [
            m for m in self._history
            if m.to_agent == agent_id or m.is_broadcast()
        ][-n:]

    def messages_by_topic(self, topic: AgentMessageTopic, n: int = 50) -> List[AgentMessage]:
        return [m for m in self._history if m.topic == topic][-n:]

    @property
    def registered_agents(self) -> set:
        return set(self._agents)

    def __len__(self) -> int:
        return len(self._history)
