from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List


QUEUE_STUCK_THRESHOLD = int(os.environ.get("ANDIE_SELF_HEAL_QUEUE_STUCK_THRESHOLD", "5"))
HIGH_CPU_THRESHOLD = float(os.environ.get("ANDIE_SELF_HEAL_HIGH_CPU_THRESHOLD", "90"))


def detect_issues(state: Dict[str, Any], last_events: Iterable[Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    last_events = list(last_events or [])
    nodes = state.get("nodes") or {}

    if state.get("queue", 0) > QUEUE_STUCK_THRESHOLD and state.get("queueDelta", 0) == 0:
        issues.append(
            {
                "type": "queue_stuck",
                "queue": state.get("queue", 0),
                "queueDelta": state.get("queueDelta", 0),
            }
        )

    for event in last_events:
        if event.get("type") != "workflow_step_complete":
            continue
        result = event.get("result") or {}
        status = (result.get("status") or event.get("status") or "").lower()
        if status not in {"failed", "error"}:
            continue
        issues.append(
            {
                "type": "agent_failure",
                "agent": event.get("step") or result.get("agent") or "unknown",
                "workflowId": event.get("workflowId"),
                "status": status,
            }
        )

    if state.get("cpu", 0) > HIGH_CPU_THRESHOLD:
        issues.append(
            {
                "type": "high_cpu",
                "cpu": state.get("cpu", 0),
            }
        )

    for node_id, node in nodes.items():
        if node.get("role") != "worker":
            continue
        if node.get("status") in {"offline", "unhealthy"}:
            issues.append(
                {
                    "type": "node_failure",
                    "node": node_id,
                    "role": node.get("role"),
                    "status": node.get("status"),
                }
            )

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for issue in issues:
        key = (issue.get("type"), issue.get("agent"), issue.get("workflowId"), issue.get("node"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped