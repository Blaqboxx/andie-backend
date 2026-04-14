from __future__ import annotations

import os
from typing import Any, Dict

import requests

from interfaces.api.node_metrics import system_metrics
from interfaces.api.node_registry import load_node_registry
from interfaces.api.node_scoring import score_node


MAX_LOAD_PER_CPU = float(os.environ.get("ANDIE_NODE_MAX_LOAD_PER_CPU", "1.5"))
MAX_MEMORY_PERCENT = float(os.environ.get("ANDIE_NODE_MAX_MEMORY_PERCENT", "85"))


def _node_metrics(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cpuPercent": None,
        "loadPerCpu": None,
        "memoryUsedPercent": None,
        "latencyMs": None,
        "role": node.get("role"),
    }


def _metrics_url(node: Dict[str, Any]) -> str | None:
    host = node.get("host")
    port = node.get("port")
    if not host or not port:
        return None
    return f"http://{host}:{port}/metrics"


def _collect_remote_metrics(node: Dict[str, Any], timeout_seconds: float) -> Dict[str, Any]:
    metrics_url = _metrics_url(node)
    if not metrics_url:
        return _node_metrics(node)

    try:
        response = requests.get(metrics_url, timeout=timeout_seconds)
        if not response.ok:
            return _node_metrics(node)
        payload = response.json()
        return {
            "cpuPercent": payload.get("cpuPercent"),
            "loadPerCpu": payload.get("loadPerCpu"),
            "memoryUsedPercent": payload.get("memoryUsedPercent"),
            "latencyMs": round(response.elapsed.total_seconds() * 1000, 2),
            "role": payload.get("role", node.get("role")),
            "cpuCount": payload.get("cpuCount"),
            "loadAverage": payload.get("loadAverage"),
            "collectedAt": payload.get("collectedAt"),
        }
    except Exception:
        return _node_metrics(node)


def _is_local_node(node: Dict[str, Any]) -> bool:
    host = (node.get("host") or "").strip()
    return host in {"127.0.0.1", "localhost", "0.0.0.0"}


def collect_node_metrics(node: Dict[str, Any], timeout_seconds: float = 2.0) -> Dict[str, Any]:
    if _is_local_node(node):
        return {**system_metrics(node.get("role", "unknown")), "latencyMs": 0.0}
    return _collect_remote_metrics(node, timeout_seconds)


def check_node_health(timeout_seconds: float = 2.0) -> Dict[str, Dict[str, Any]]:
    nodes = load_node_registry()
    for node_id, node in nodes.items():
        metrics = collect_node_metrics(node, timeout_seconds=timeout_seconds)
        entry = {
            **node,
            "target": f"{node.get('host') or 'unconfigured'}:{node.get('port') or 'n/a'}",
            "metrics": metrics,
            "available": False,
            "overloaded": False,
        }
        health_url = node.get("healthUrl")
        if not health_url:
            entry["status"] = "offline"
            nodes[node_id] = entry
            continue

        try:
            response = requests.get(health_url, timeout=timeout_seconds)
            entry["status"] = "healthy" if response.ok else "unhealthy"
            entry["available"] = response.ok
        except Exception:
            entry["status"] = "offline"
            entry["available"] = False

        load = metrics.get("loadPerCpu")
        memory = metrics.get("memoryUsedPercent")
        entry["overloaded"] = bool(
            (load is not None and load >= MAX_LOAD_PER_CPU)
            or (memory is not None and memory >= MAX_MEMORY_PERCENT)
        )
        entry["score"] = score_node(entry)

        nodes[node_id] = entry
    return nodes