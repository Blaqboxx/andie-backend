"""
Dependency Engine — topological ordering, readiness resolution, and
failure propagation for ANDIE's task graph.

Responsibilities
----------------
1. Validate the graph (no cycles, no dangling dependency references)
2. Topological sort — produce a valid execution order
3. Resolve which nodes are READY given current statuses
4. Propagate BLOCKED status when a dependency fails
5. Identify nodes that can run in parallel

This module is purely graph-level logic — it has no knowledge of LLMs,
sandboxes, or execution.  It operates only on TaskNode objects.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from .task_models import TaskNode, TaskStatus


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CyclicDependencyError(Exception):
    """Raised when the task graph contains a cycle."""


class DanglingDependencyError(Exception):
    """Raised when a task depends on a node ID that doesn't exist."""


# ---------------------------------------------------------------------------
# DependencyEngine
# ---------------------------------------------------------------------------


class DependencyEngine:
    """
    Stateless graph-analysis engine.

    All methods accept a list or dict of TaskNodes and return derived
    information without mutating the nodes (except ``propagate_failures``,
    which *does* mutate status/failure_reason on BLOCKED nodes by design —
    that state change must be visible to the task graph executor).
    """

    # ------------------------------------------------------------------ #
    # Validation                                                           #
    # ------------------------------------------------------------------ #

    def validate(self, nodes: List[TaskNode]) -> None:
        """
        Validate graph structure.

        Raises
        ------
        DanglingDependencyError
            If any dependency ID refers to a node not in *nodes*.
        CyclicDependencyError
            If the graph contains a cycle (not a DAG).
        """
        id_set: Set[str] = {n.id for n in nodes}
        for node in nodes:
            for dep_id in node.dependencies:
                if dep_id not in id_set:
                    raise DanglingDependencyError(
                        f"Task '{node.id}' depends on '{dep_id}' which does not exist in the graph."
                    )
        # Cycle check via DFS
        self._assert_acyclic(nodes)

    def _assert_acyclic(self, nodes: List[TaskNode]) -> None:
        """Kahn's algorithm — raise CyclicDependencyError if graph has a cycle."""
        adj: Dict[str, List[str]] = {n.id: list(n.dependencies) for n in nodes}
        # Build reverse: who does each node block?
        in_degree: Dict[str, int] = {n.id: len(n.dependencies) for n in nodes}
        dependents: Dict[str, List[str]] = {n.id: [] for n in nodes}
        for node in nodes:
            for dep_id in node.dependencies:
                dependents[dep_id].append(node.id)

        queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
        processed = 0
        while queue:
            nid = queue.popleft()
            processed += 1
            for child in dependents[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if processed != len(nodes):
            # Some nodes were never processed → cycle
            cycle_members = [nid for nid, deg in in_degree.items() if deg > 0]
            raise CyclicDependencyError(
                f"Cycle detected among nodes: {cycle_members}"
            )

    # ------------------------------------------------------------------ #
    # Topological ordering                                                 #
    # ------------------------------------------------------------------ #

    def topological_sort(self, nodes: List[TaskNode]) -> List[TaskNode]:
        """
        Return nodes in a valid execution order (all deps before dependents).
        Nodes at the same level (no ordering constraint between them) are
        sorted by priority (critical first) then by insertion order.
        """
        node_map: Dict[str, TaskNode] = {n.id: n for n in nodes}
        in_degree: Dict[str, int] = {n.id: 0 for n in nodes}
        dependents: Dict[str, List[str]] = {n.id: [] for n in nodes}

        for node in nodes:
            for dep_id in node.dependencies:
                in_degree[node.id] += 1
                dependents[dep_id].append(node.id)

        _priority_order = {
            "critical": 0, "high": 1, "medium": 2, "low": 3,
        }

        # Use a list as a priority-aware queue
        ready: List[TaskNode] = sorted(
            [n for n in nodes if in_degree[n.id] == 0],
            key=lambda n: _priority_order.get(n.priority.value, 99),
        )
        result: List[TaskNode] = []

        while ready:
            node = ready.pop(0)
            result.append(node)
            children = sorted(
                dependents[node.id],
                key=lambda nid: _priority_order.get(node_map[nid].priority.value, 99),
            )
            for child_id in children:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    ready.append(node_map[child_id])
                    ready.sort(key=lambda n: _priority_order.get(n.priority.value, 99))

        return result

    # ------------------------------------------------------------------ #
    # Readiness resolution                                                 #
    # ------------------------------------------------------------------ #

    def ready_nodes(self, nodes: List[TaskNode]) -> List[TaskNode]:
        """
        Return nodes whose dependencies are all SUCCESS and that are
        currently PENDING (i.e., they can start executing now).
        """
        node_map: Dict[str, TaskNode] = {n.id: n for n in nodes}
        result: List[TaskNode] = []
        for node in nodes:
            if node.status != TaskStatus.PENDING:
                continue
            deps_satisfied = all(
                node_map.get(dep_id, None) is not None
                and node_map[dep_id].status == TaskStatus.SUCCESS
                for dep_id in node.dependencies
            )
            if deps_satisfied:
                result.append(node)
        return result

    def parallel_groups(self, nodes: List[TaskNode]) -> List[List[TaskNode]]:
        """
        Group nodes by execution wave — nodes in the same group have no
        dependency relationship with each other and can run in parallel.

        Returns a list of groups in topological order.
        """
        node_map: Dict[str, TaskNode] = {n.id: n for n in nodes}
        levels: Dict[str, int] = {}

        def _level(nid: str) -> int:
            if nid in levels:
                return levels[nid]
            node = node_map[nid]
            if not node.dependencies:
                levels[nid] = 0
            else:
                levels[nid] = max(_level(dep) for dep in node.dependencies) + 1
            return levels[nid]

        for node in nodes:
            _level(node.id)

        max_level = max(levels.values(), default=0)
        groups: List[List[TaskNode]] = [[] for _ in range(max_level + 1)]
        for node in nodes:
            groups[levels[node.id]].append(node)

        return [g for g in groups if g]

    # ------------------------------------------------------------------ #
    # Failure propagation                                                  #
    # ------------------------------------------------------------------ #

    def propagate_failures(self, nodes: List[TaskNode]) -> List[TaskNode]:
        """
        Propagate BLOCKED status through the graph.

        For every node whose status is FAILED, all transitive dependents
        that are still PENDING are marked BLOCKED.

        Returns the list of newly blocked nodes.
        """
        node_map: Dict[str, TaskNode] = {n.id: n for n in nodes}
        # Build forward dependency graph: who does each node enable?
        dependents: Dict[str, List[str]] = {n.id: [] for n in nodes}
        for node in nodes:
            for dep_id in node.dependencies:
                dependents[dep_id].append(node.id)

        # Collect all currently failed nodes
        failed_ids: Set[str] = {n.id for n in nodes if n.status == TaskStatus.FAILED}
        newly_blocked: List[TaskNode] = []

        # BFS through the dependent chain
        queue: deque[str] = deque(failed_ids)
        visited: Set[str] = set(failed_ids)

        while queue:
            src_id = queue.popleft()
            src_node = node_map[src_id]
            for child_id in dependents[src_id]:
                if child_id in visited:
                    continue
                visited.add(child_id)
                child = node_map[child_id]
                if child.status in (TaskStatus.PENDING, TaskStatus.READY):
                    child.mark_blocked(because_of=src_id)
                    newly_blocked.append(child)
                    queue.append(child_id)  # propagate further

        return newly_blocked

    # ------------------------------------------------------------------ #
    # Graph introspection helpers                                          #
    # ------------------------------------------------------------------ #

    def is_complete(self, nodes: List[TaskNode]) -> bool:
        """True when all nodes have reached a terminal state."""
        return all(n.is_terminal for n in nodes)

    def critical_path(self, nodes: List[TaskNode]) -> List[str]:
        """
        Return the IDs of CRITICAL-priority nodes in topological order.
        These must all succeed for the plan to be considered successful.
        """
        sorted_nodes = self.topological_sort(nodes)
        return [n.id for n in sorted_nodes if n.priority.value == "critical"]

    def stats(self, nodes: List[TaskNode]) -> Dict[str, int]:
        """Return a status count dict for quick reporting."""
        counts: Dict[str, int] = {s.value: 0 for s in TaskStatus}
        for node in nodes:
            counts[node.status.value] += 1
        return counts
