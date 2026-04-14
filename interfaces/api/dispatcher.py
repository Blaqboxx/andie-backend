from __future__ import annotations

from typing import Any, Dict

from interfaces.api.node_monitor import check_node_health
from interfaces.api.node_scoring import score_node


def classify_task(task_text: str, params: Dict[str, Any] | None = None) -> str:
    params = params or {}
    if params.get("preferWorker") or params.get("prefer_worker"):
        return "compute_heavy"

    if params.get("preferLocal") or params.get("prefer_local"):
        return "low_latency"

    lowered = (task_text or "").lower()
    heavy_markers = (
        "heavy",
        "batch",
        "deep scan",
        "full scan",
        "full rebuild",
        "repo-wide",
        "analyze the codebase",
        "analyze codebase",
        "index all",
        "multi-agent",
        "autonomous loop",
    )
    if any(marker in lowered for marker in heavy_markers):
        return "compute_heavy"

    low_latency_markers = (
        "quick",
        "status",
        "health",
        "ping",
        "fast",
        "low latency",
    )
    if any(marker in lowered for marker in low_latency_markers):
        return "low_latency"

    return "default"


def get_best_node(task_type: str = "default", preferred_node: str | None = None) -> Dict[str, Any]:
    nodes = check_node_health()
    for node in nodes.values():
        node["score"] = score_node(node, task_type=task_type) if node.get("available") else None

    ranked_candidates = [
        {"node": node_id, "score": node.get("score")}
        for node_id, node in sorted(
            ((node_id, node) for node_id, node in nodes.items() if node.get("available")),
            key=lambda item: item[1].get("score") if item[1].get("score") is not None else 9999,
        )
    ]

    if preferred_node in nodes and nodes[preferred_node].get("available"):
        node = nodes[preferred_node]
        return {
            "node": preferred_node,
            "reason": "preferred_node_available",
            "endpoint": node.get("executeUrl"),
            "score": node.get("score"),
            "rankedCandidates": ranked_candidates,
            "nodes": nodes,
        }

    if ranked_candidates:
        selected = ranked_candidates[0]
        return {
            "node": selected["node"],
            "reason": f"{task_type}_lowest_score" if task_type != "default" else "lowest_score",
            "endpoint": nodes[selected["node"]].get("executeUrl"),
            "score": selected.get("score"),
            "rankedCandidates": ranked_candidates,
            "nodes": nodes,
        }

    return {
        "node": "thinkpad",
        "reason": "no_healthy_nodes_fallback",
        "endpoint": nodes["thinkpad"].get("executeUrl"),
        "score": None,
        "rankedCandidates": ranked_candidates,
        "nodes": nodes,
    }


def dispatch_task(task: str, task_type: str = "default", preferred_node: str | None = None) -> Dict[str, Any]:
    target = get_best_node(task_type=task_type, preferred_node=preferred_node)
    return {
        "targetNode": target["node"],
        "endpoint": target.get("endpoint"),
        "reason": target.get("reason"),
        "taskType": task_type,
        "score": target.get("score"),
        "rankedCandidates": target.get("rankedCandidates") or [],
        "task": task,
        "nodes": target.get("nodes") or {},
    }