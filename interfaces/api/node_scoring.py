from __future__ import annotations

from typing import Any, Dict


def score_node(node: Dict[str, Any], task_type: str = "default") -> float:
    metrics = node.get("metrics") or {}
    load = metrics.get("loadPerCpu")
    memory = metrics.get("memoryUsedPercent")
    latency_ms = metrics.get("latencyMs")

    load_score = float(load if load is not None else 10.0)
    memory_score = float(memory if memory is not None else 100.0) / 100.0
    latency_score = float(latency_ms if latency_ms is not None else 1000.0) / 1000.0

    if task_type == "compute_heavy":
        score = (load_score * 0.5) + (memory_score * 0.35) + (latency_score * 0.15)
        if node.get("role") == "brain":
            score += 0.2
    elif task_type == "low_latency":
        score = (latency_score * 0.6) + (load_score * 0.25) + (memory_score * 0.15)
        if node.get("role") == "worker":
            score += 0.15
    else:
        score = (load_score * 0.4) + (memory_score * 0.3) + (latency_score * 0.3)

    if node.get("overloaded"):
        score += 1.0

    return round(score, 4)