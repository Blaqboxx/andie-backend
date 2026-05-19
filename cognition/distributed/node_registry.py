"""
Node Registry — ANDIE's infrastructure awareness memory.

NodeRegistry maintains the authoritative map of every known cluster node.
It is the single source of truth for:

  * node identity (hostname, role, capabilities, tags)
  * live utilization (updated by HealthMonitor)
  * capability-based queries (find GPU node, find Ollama node, …)
  * load-ranked node selection

A "local" node (this machine) is automatically registered at startup using
psutil if available, so single-machine deployments work without any config.

Thread-safety: the registry uses a plain dict protected by no lock because
all mutations happen from the asyncio event loop in a single-threaded manner.
If you expose the registry across threads, wrap mutations with asyncio.Lock.
"""

from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from typing import Callable, Dict, Iterator, List, Optional

from .node_models import (
    NodeCapability,
    NodeHealthReport,
    NodeRole,
    NodeState,
    NodeStatus,
)

log = logging.getLogger(__name__)

_LOCAL_NODE_ID = "local"


def _build_local_node() -> NodeState:
    """
    Construct a NodeState representing this machine.

    Capabilities are inferred from available packages:
      - GENERAL always present
      - OLLAMA if reachable at localhost:11434
      - DOCKER if /var/run/docker.sock exists
    """
    caps: List[NodeCapability] = [NodeCapability.GENERAL]

    try:
        import socket as _s
        _s.create_connection(("localhost", 11434), timeout=0.5).close()
        caps.append(NodeCapability.OLLAMA)
        caps.append(NodeCapability.LLM_SERVING)
    except Exception:
        pass

    try:
        import os
        if os.path.exists("/var/run/docker.sock"):
            caps.append(NodeCapability.DOCKER)
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "localhost"

    return NodeState(
        node_id=_LOCAL_NODE_ID,
        hostname=hostname,
        role=NodeRole.LOCAL,
        capabilities=caps,
        tags=["local"],
        status=NodeStatus.ONLINE,
        last_seen=datetime.now(timezone.utc),
    )


class NodeRegistry:
    """
    Infrastructure awareness memory for ANDIE's distributed cognition layer.

    Usage::

        registry = NodeRegistry()

        # Register a remote node
        registry.register(NodeState(
            node_id="nuc-main",
            hostname=os.environ.get("INFERENCE_NODE_HOST", "host.docker.internal"),
            role=NodeRole.AI_SERVER,
            capabilities=[NodeCapability.GPU_INFERENCE, NodeCapability.OLLAMA],
        ))

        # Query
        gpu_nodes = registry.nodes_with_capability(NodeCapability.GPU_INFERENCE)
        best      = registry.best_node_for([NodeCapability.GPU_INFERENCE])
    """

    def __init__(self, *, auto_register_local: bool = True) -> None:
        self._nodes: Dict[str, NodeState] = {}
        if auto_register_local:
            local = _build_local_node()
            self._nodes[local.node_id] = local
            log.info(
                "NodeRegistry: local node '%s' registered (caps=%s)",
                local.hostname,
                [c.value for c in local.capabilities],
            )

    # ------------------------------------------------------------------ #
    # Registration                                                         #
    # ------------------------------------------------------------------ #

    def register(self, node: NodeState) -> None:
        """Add or fully replace a node entry."""
        self._nodes[node.node_id] = node
        log.info(
            "NodeRegistry: registered '%s' @ %s [%s]",
            node.node_id,
            node.hostname,
            node.role.value,
        )

    def deregister(self, node_id: str) -> bool:
        """Remove a node. Returns True if it existed."""
        existed = node_id in self._nodes
        self._nodes.pop(node_id, None)
        return existed

    # ------------------------------------------------------------------ #
    # Health update (called by HealthMonitor)                              #
    # ------------------------------------------------------------------ #

    def apply_health_report(self, report: NodeHealthReport) -> NodeState | None:
        """
        Update a node's live metrics from a HealthMonitor poll result.

        Returns the updated NodeState, or None if the node_id is unknown.
        """
        node = self._nodes.get(report.node_id)
        if node is None:
            log.debug("NodeRegistry: unknown node '%s' in health report", report.node_id)
            return None

        if report.reachable:
            node.update_metrics(
                cpu=report.cpu,
                ram=report.ram,
                gpu=report.gpu,
                disk=report.disk,
                status=NodeStatus.ONLINE,
            )
        else:
            node.status = NodeStatus.OFFLINE
            node.last_seen = node.last_seen  # keep previous last_seen

        return node

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    def get(self, node_id: str) -> Optional[NodeState]:
        return self._nodes.get(node_id)

    def all_nodes(self) -> List[NodeState]:
        return list(self._nodes.values())

    def available_nodes(self) -> List[NodeState]:
        """Nodes that are ONLINE or DEGRADED."""
        return [n for n in self._nodes.values() if n.is_available]

    def nodes_with_capability(self, cap: NodeCapability) -> List[NodeState]:
        """Available nodes advertising a given capability."""
        return [n for n in self.available_nodes() if n.has_capability(cap)]

    def best_node_for(
        self,
        required_caps: List[NodeCapability],
        *,
        exclude: Optional[List[str]] = None,
    ) -> Optional[NodeState]:
        """
        Return the least-loaded available node that satisfies all required
        capabilities.  Returns None if no capable node is available.

        Parameters
        ----------
        required_caps:
            Every listed capability must be present.
        exclude:
            Node IDs to skip (e.g., nodes that already failed this task).
        """
        candidates = self.available_nodes()
        if exclude:
            candidates = [n for n in candidates if n.node_id not in exclude]

        if required_caps:
            candidates = [
                n for n in candidates
                if all(n.has_capability(c) for c in required_caps)
            ]

        if not candidates:
            return None

        return min(candidates, key=lambda n: n.load_score)

    def local_node(self) -> Optional[NodeState]:
        return self._nodes.get(_LOCAL_NODE_ID)

    # ------------------------------------------------------------------ #
    # Iteration                                                            #
    # ------------------------------------------------------------------ #

    def __iter__(self) -> Iterator[NodeState]:
        return iter(self._nodes.values())

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._nodes

    # ------------------------------------------------------------------ #
    # Introspection                                                         #
    # ------------------------------------------------------------------ #

    def summary(self) -> List[Dict]:
        return [n.to_dict() for n in self._nodes.values()]
