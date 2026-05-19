"""
Node Scheduler — distributed workload routing for STEP 9.

NodeScheduler reads TaskNode metadata and selects the optimal cluster node
for each task using a priority-based decision tree:

  Priority 1 — ``target:<node_id>`` tag → direct pinning (always honoured)
  Priority 2 — ``requires:<capability>`` tags → capability-filtered selection
  Priority 3 — Lowest load score among capable, available nodes
  Priority 4 — Local node fallback if no remote nodes match

Tag contract (on TaskNode.tags):
  ``"target:nuc-main"``           — pin task to specific node
  ``"requires:gpu_inference"``    — must run on a GPU-capable node
  ``"requires:ollama"``           — needs Ollama service
  ``"no_llm"``                    — skip if scheduler says postpone_llm

Returned RoutingDecision includes the full rationale so the event bus
can surface infrastructure decisions to operators / dashboards.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .node_models import NodeCapability, NodeState, RoutingDecision
from .node_registry import NodeRegistry

log = logging.getLogger(__name__)

_REQUIRES_PREFIX = "requires:"
_TARGET_PREFIX   = "target:"
_NO_LLM_TAG      = "no_llm"


class NodeScheduler:
    """
    Distributed workload router.

    Usage::

        registry  = NodeRegistry()
        scheduler = NodeScheduler(registry)

        decision = scheduler.route(task_id="deploy-api", task_tags=["requires:docker"])
        print(decision.selected_node_id, decision.reason)
    """

    def __init__(
        self,
        registry: NodeRegistry,
        *,
        postpone_llm: bool = False,
    ) -> None:
        self._registry     = registry
        self._postpone_llm = postpone_llm

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def route(
        self,
        task_id: str,
        task_tags: Optional[List[str]] = None,
        *,
        exclude: Optional[List[str]] = None,
    ) -> RoutingDecision:
        """
        Select the best cluster node for a task.

        Parameters
        ----------
        task_id:
            Unique identifier of the task being routed (used in the decision).
        task_tags:
            Tags from ``TaskNode.tags`` — parsed for ``target:`` and
            ``requires:`` directives.
        exclude:
            Node IDs that should not be considered (e.g., previously failed).

        Returns
        -------
        RoutingDecision — always non-None; worst case falls back to local.
        """
        tags = task_tags or []

        # ── Priority 1: direct pinning ────────────────────────────────
        pinned_id = self._parse_target(tags)
        if pinned_id:
            node = self._registry.get(pinned_id)
            if node and node.is_available:
                return RoutingDecision(
                    task_id=task_id,
                    selected_node_id=node.node_id,
                    selected_hostname=node.hostname,
                    reason=f"Pinned via tag 'target:{pinned_id}'",
                    is_local=node.node_id == "local",
                    candidates_seen=1,
                    load_score=node.load_score,
                )
            # Pin target unreachable — warn and fall through to capability routing
            log.warning(
                "NodeScheduler: pinned target '%s' unavailable for task '%s'; falling through",
                pinned_id,
                task_id,
            )

        # ── Priority 2 & 3: capability + load routing ─────────────────
        required_caps = self._parse_capabilities(tags)

        # Apply LLM postponement: if the resource scheduler signals high GPU/RAM
        # pressure and the task has `requires:gpu_inference` or `requires:llm_serving`,
        # we prefer non-GPU nodes.  The task still executes; it just lands on a
        # less-loaded node (or local).
        skip_gpu = self._postpone_llm and (
            NodeCapability.GPU_INFERENCE in required_caps
            or NodeCapability.LLM_SERVING in required_caps
        )

        candidates = self._registry.available_nodes()
        if exclude:
            candidates = [n for n in candidates if n.node_id not in exclude]

        capable = [
            n for n in candidates
            if all(n.has_capability(c) for c in required_caps)
            and not (skip_gpu and n.has_capability(NodeCapability.GPU_INFERENCE))
        ]

        if capable:
            best = min(capable, key=lambda n: n.load_score)
            return RoutingDecision(
                task_id=task_id,
                selected_node_id=best.node_id,
                selected_hostname=best.hostname,
                reason=(
                    f"Best-load node for caps={[c.value for c in required_caps]} "
                    f"(score={best.load_score:.1f})"
                ),
                is_local=best.node_id == "local",
                candidates_seen=len(capable),
                load_score=best.load_score,
                required_caps=[c.value for c in required_caps],
            )

        # ── Priority 4: fallback to local ─────────────────────────────
        local = self._registry.local_node()
        if local:
            return RoutingDecision(
                task_id=task_id,
                selected_node_id=local.node_id,
                selected_hostname=local.hostname,
                reason=f"Fallback to local — no capable remote node for caps={[c.value for c in required_caps]}",
                is_local=True,
                is_fallback=True,
                candidates_seen=len(candidates),
                load_score=local.load_score,
                required_caps=[c.value for c in required_caps],
            )

        # Should never reach here unless registry is completely empty
        return RoutingDecision(
            task_id=task_id,
            selected_node_id="local",
            selected_hostname="localhost",
            reason="No nodes available — defaulting to local",
            is_local=True,
            is_fallback=True,
        )

    def set_postpone_llm(self, value: bool) -> None:
        """Toggle LLM-pressure-aware routing (called by ResourceAwareScheduler)."""
        self._postpone_llm = value

    # ------------------------------------------------------------------ #
    # Tag parsers                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_target(tags: List[str]) -> Optional[str]:
        for tag in tags:
            if tag.startswith(_TARGET_PREFIX):
                return tag[len(_TARGET_PREFIX):]
        return None

    @staticmethod
    def _parse_capabilities(tags: List[str]) -> List[NodeCapability]:
        caps: List[NodeCapability] = []
        for tag in tags:
            if tag.startswith(_REQUIRES_PREFIX):
                cap_str = tag[len(_REQUIRES_PREFIX):]
                try:
                    caps.append(NodeCapability(cap_str))
                except ValueError:
                    log.debug("NodeScheduler: unknown capability '%s'", cap_str)
        return caps

    # ------------------------------------------------------------------ #
    # Introspection                                                        #
    # ------------------------------------------------------------------ #

    def explain(
        self,
        task_id: str,
        task_tags: Optional[List[str]] = None,
    ) -> str:
        """
        Return a human-readable routing plan string without executing anything.

        Useful for debugging and UI display.
        """
        decision = self.route(task_id, task_tags)
        lines = [
            f"Task '{task_id}' routing decision:",
            f"  → node:     {decision.selected_node_id} ({decision.selected_hostname})",
            f"  → reason:   {decision.reason}",
            f"  → local:    {decision.is_local}",
            f"  → fallback: {decision.is_fallback}",
            f"  → load:     {decision.load_score:.1f}",
        ]
        return "\n".join(lines)
