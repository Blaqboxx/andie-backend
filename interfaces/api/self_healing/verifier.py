from __future__ import annotations

from typing import Any, Dict


def verify_recovery(state: Dict[str, Any], issue: Dict[str, Any] | None = None) -> bool:
    issue_type = (issue or {}).get("type")

    if issue_type == "queue_stuck":
        return state.get("queue", 0) < 3

    if issue_type == "high_cpu":
        return state.get("cpu", 0) < 80

    if issue_type == "agent_failure":
        return state.get("failed", 0) == 0 and state.get("queue", 0) < 5

    if issue_type == "node_failure":
        nodes = state.get("nodes") or {}
        failed_node = nodes.get((issue or {}).get("node") or "", {})
        brain_node = nodes.get("thinkpad") or {}
        return not failed_node.get("available", False) and brain_node.get("available", False)

    return state.get("queue", 0) < 3 and state.get("cpu", 0) < 80