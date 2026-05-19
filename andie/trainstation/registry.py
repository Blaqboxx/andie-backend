"""
registry.py — Live service registry for ANDIE stack.
Tracks each service's status, last health check, and metadata.
Thread-safe in-process store. Exposed via /registry endpoint.
"""
from __future__ import annotations
import threading
import time
from typing import Literal

ServiceStatus = Literal["online", "degraded", "offline", "starting", "unknown"]

_lock = threading.Lock()

_REGISTRY: dict[str, dict] = {
    "backend":  {"status": "unknown", "port": 8010, "url": "http://localhost:8010", "critical": True},
    "ui":       {"status": "unknown", "port": 5173, "url": "http://localhost:5173", "critical": False},
    "ollama":   {"status": "unknown", "port": 11434, "url": "http://192.168.50.9:11434", "critical": True},
    "redis":    {"status": "unknown", "port": 6379, "url": None, "critical": False},
    "qdrant":   {"status": "unknown", "port": 6333, "url": None, "critical": False},
    "guardian": {"status": "unknown", "port": 7010, "url": "http://localhost:7010", "critical": False},
    "mcp":      {"status": "unknown", "port": 7001, "url": "http://localhost:7001", "critical": False},
}


def update(service: str, status: ServiceStatus, detail: str = "", latency_ms: int = 0, **extra):
    with _lock:
        if service not in _REGISTRY:
            _REGISTRY[service] = {}
        _REGISTRY[service].update({
            "status": status,
            "detail": detail,
            "latency_ms": latency_ms,
            "last_check": time.time(),
            **extra,
        })


def get(service: str) -> dict:
    with _lock:
        return dict(_REGISTRY.get(service, {"status": "unknown"}))


def snapshot() -> dict:
    with _lock:
        result = {}
        for name, data in _REGISTRY.items():
            entry = dict(data)
            # Human-readable last_check
            lc = entry.get("last_check")
            entry["last_check_ago"] = f"{round(time.time() - lc)}s ago" if lc else "never"
            result[name] = entry

    # Overall health roll-up
    statuses = [v["status"] for v in result.values()]
    critical_services = [k for k, v in _REGISTRY.items() if v.get("critical")]
    critical_statuses = [result[s]["status"] for s in critical_services if s in result]

    if any(s in ("offline", "unknown") for s in critical_statuses):
        overall = "degraded"
    elif any(s == "degraded" for s in statuses):
        overall = "degraded"
    elif all(s == "online" for s in critical_statuses):
        overall = "healthy"
    else:
        overall = "starting"

    return {"overall": overall, "services": result}


def mark_starting(service: str):
    update(service, "starting", "startup initiated")


def mark_online(service: str, latency_ms: int = 0, detail: str = ""):
    update(service, "online", detail or "healthy", latency_ms=latency_ms)


def mark_offline(service: str, reason: str = ""):
    update(service, "offline", reason or "unreachable")
