"""
STEP 10A — Consensus Engine
============================
Multi-agent consensus validation for critical decisions.

Workflow
--------
1. Caller calls ``ConsensusEngine.request(topic, payload, ...)``
2. Engine resolves participant agents (explicit list or all CONSENSUS_VOTING agents)
3. Broadcasts a CONSENSUS_REQUEST message via MessageBus
4. Calls each agent's vote function concurrently (with timeout)
5. Tallies yes / no / abstained votes
6. Returns ConsensusResult: approved when yes_votes / total >= required_approval_ratio

Custom vote functions
---------------------
Register per-agent async vote functions via ``register_vote_fn(agent_id, fn)``.
Signature:  async def fn(request: ConsensusRequest, agent: AgentState) -> ConsensusVote

The default evaluator approves when:
- agent has CONSENSUS_VOTING capability, AND
- agent.confidence >= 0.5
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .agent_models import (
    AgentCapability,
    AgentMessageTopic,
    AgentState,
    ConsensusRequest,
    ConsensusResult,
    ConsensusVote,
)
from .agent_registry import AgentRegistry
from .message_bus import MessageBus

logger = logging.getLogger(__name__)

# Async callable (ConsensusRequest, AgentState) → ConsensusVote
VoteFn = Callable[[ConsensusRequest, AgentState], Coroutine]


async def _default_vote(request: ConsensusRequest, agent: AgentState) -> ConsensusVote:
    """Built-in evaluator: approve when capable + confident."""
    capable    = agent.has_capability(AgentCapability.CONSENSUS_VOTING)
    confident  = agent.confidence >= 0.5
    approved   = capable and confident
    return ConsensusVote(
        request_id = request.request_id,
        agent_id   = agent.agent_id,
        approved   = approved,
        reason     = "capable+confident" if approved else (
            "no_capability" if not capable else "low_confidence"
        ),
        confidence = agent.confidence,
    )


class ConsensusEngine:
    """Coordinates multi-agent consensus for critical decisions."""

    def __init__(
        self,
        registry: AgentRegistry,
        bus:      MessageBus,
        default_timeout:        float = 5.0,
        default_approval_ratio: float = 0.67,
    ) -> None:
        self._registry       = registry
        self._bus            = bus
        self._default_timeout = default_timeout
        self._default_ratio  = default_approval_ratio
        self._vote_fns: Dict[str, VoteFn] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register_vote_fn(self, agent_id: str, fn: VoteFn) -> None:
        """Register a custom async vote function for an agent."""
        self._vote_fns[agent_id] = fn

    # ── Core ──────────────────────────────────────────────────────────────────

    async def request(
        self,
        topic:                   str,
        payload:                 Dict[str, Any],
        participants:            Optional[List[str]] = None,
        required_approval_ratio: Optional[float]     = None,
        timeout:                 Optional[float]     = None,
        requester:               str                 = "coordinator",
    ) -> ConsensusResult:
        """Run a consensus round and return the aggregated result."""
        ratio     = required_approval_ratio if required_approval_ratio is not None else self._default_ratio
        timeout_s = timeout if timeout is not None else self._default_timeout

        # Resolve participants
        if participants:
            agents: List[AgentState] = [
                a for pid in participants
                if (a := self._registry.get(pid)) is not None and a.is_available()
            ]
        else:
            agents = [
                a for a in self._registry.agents_with_capability(AgentCapability.CONSENSUS_VOTING)
                if a.is_available()
            ]

        consensus_req = ConsensusRequest(
            topic                   = topic,
            payload                 = payload,
            required_approval_ratio = ratio,
            timeout_seconds         = timeout_s,
            participants            = [a.agent_id for a in agents],
        )

        # No eligible agents → auto-reject
        if not agents:
            return ConsensusResult(
                request_id       = consensus_req.request_id,
                topic            = topic,
                approved         = False,
                total_votes      = 0,
                yes_votes        = 0,
                no_votes         = 0,
                abstained        = 0,
                approval_ratio   = 0.0,
                final_confidence = 0.0,
                dissenting_reasons = ["No eligible agents available for consensus"],
                participants     = [],
            )

        # Notify via bus (fire-and-forget, handlers are optional)
        await self._bus.broadcast(
            from_agent = requester,
            topic      = AgentMessageTopic.CONSENSUS_REQUEST,
            payload    = {
                "request_id":              consensus_req.request_id,
                "topic":                   topic,
                "payload":                 payload,
                "required_approval_ratio": ratio,
            },
        )

        # Collect votes concurrently with timeout
        async def _collect() -> List[ConsensusVote]:
            coros = [
                self._vote_fns.get(a.agent_id, _default_vote)(consensus_req, a)
                for a in agents
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)
            votes: List[ConsensusVote] = []
            for r in results:
                if isinstance(r, ConsensusVote):
                    votes.append(r)
                elif isinstance(r, Exception):
                    logger.warning("Vote raised exception: %s", r)
            return votes

        try:
            votes = await asyncio.wait_for(_collect(), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning("Consensus timed out for topic '%s'", topic)
            votes = []

        yes       = [v for v in votes if v.approved]
        no        = [v for v in votes if not v.approved]
        total     = len(votes)
        abstained = len(agents) - total

        approval_ratio   = len(yes) / total if total > 0 else 0.0
        approved         = total > 0 and approval_ratio >= ratio
        final_confidence = sum(v.confidence for v in yes) / len(yes) if yes else 0.0

        result = ConsensusResult(
            request_id       = consensus_req.request_id,
            topic            = topic,
            approved         = approved,
            total_votes      = total,
            yes_votes        = len(yes),
            no_votes         = len(no),
            abstained        = abstained,
            approval_ratio   = approval_ratio,
            final_confidence = final_confidence,
            dissenting_reasons = [v.reason for v in no],
            participants     = [a.agent_id for a in agents],
        )

        # Publish outcome so all agents can observe
        await self._bus.broadcast(
            from_agent = requester,
            topic      = AgentMessageTopic.CONSENSUS_VOTE,
            payload    = result.to_dict(),
        )

        logger.info(
            "Consensus '%s': %s (%.0f%% approval, %d/%d votes)",
            topic,
            "APPROVED" if approved else "REJECTED",
            approval_ratio * 100,
            len(yes),
            total,
        )
        return result
