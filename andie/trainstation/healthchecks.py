"""
healthchecks.py — Per-service health verification.
Wraps probe_runner for use by both trainstation and registry background loop.
"""
from __future__ import annotations
import asyncio
import time
import httpx

from andie_backend.andie.trainstation import registry


HEALTH_ENDPOINTS = {
    "backend":  ("http://localhost:8010/health", False),   # (url, strict_200)
    "ui":       ("http://localhost:5173/", False),
    "ollama":   ("http://192.168.50.9:11434/api/tags", True),
    "guardian": ("http://localhost:7010/health", True),
    "mcp":      ("http://localhost:7001/status", True),
}

REDIS_CHECK = ("localhost", 6379)
QDRANT_CHECK = ("localhost", 6333)


async def _http_check(service: str, url: str, strict: bool = False) -> dict:
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(url)
        latency = round((time.monotonic() - t0) * 1000)
        if strict and r.status_code >= 400:
            registry.update(service, "degraded", f"HTTP {r.status_code}", latency_ms=latency)
            return {"service": service, "status": "degraded", "latency_ms": latency}
        registry.mark_online(service, latency_ms=latency, detail=f"HTTP {r.status_code}")
        return {"service": service, "status": "online", "latency_ms": latency}
    except httpx.ConnectError:
        registry.mark_offline(service, "connection refused")
        return {"service": service, "status": "offline"}
    except Exception as e:
        registry.update(service, "degraded", str(e)[:80])
        return {"service": service, "status": "degraded"}


async def _tcp_check(service: str, host: str, port: int) -> dict:
    import asyncio as _a
    loop = _a.get_event_loop()
    t0 = time.monotonic()
    try:
        import socket
        fut = loop.run_in_executor(None, lambda: socket.create_connection((host, port), timeout=2).close())
        await asyncio.wait_for(fut, timeout=3)
        latency = round((time.monotonic() - t0) * 1000)
        registry.mark_online(service, latency_ms=latency)
        return {"service": service, "status": "online", "latency_ms": latency}
    except Exception:
        registry.mark_offline(service, f"tcp:{host}:{port} unreachable")
        return {"service": service, "status": "offline"}


async def check_all() -> list[dict]:
    """Run all health checks in parallel. Updates registry as side-effect."""
    tasks = []
    for service, (url, strict) in HEALTH_ENDPOINTS.items():
        tasks.append(_http_check(service, url, strict))
    tasks.append(_tcp_check("redis", *REDIS_CHECK))
    tasks.append(_tcp_check("qdrant", *QDRANT_CHECK))
    return list(await asyncio.gather(*tasks, return_exceptions=True))


async def check_one(service: str) -> dict:
    if service in HEALTH_ENDPOINTS:
        url, strict = HEALTH_ENDPOINTS[service]
        return await _http_check(service, url, strict)
    if service == "redis":
        return await _tcp_check("redis", *REDIS_CHECK)
    if service == "qdrant":
        return await _tcp_check("qdrant", *QDRANT_CHECK)
    return {"service": service, "status": "unknown", "detail": "no check configured"}


# ── Background poller — runs inside backend process ──────────────────────────

_poll_task = None


async def _poll_loop(interval: int = 30):
    """Background coroutine. Poll all services every `interval` seconds."""
    while True:
        try:
            await check_all()
        except Exception:
            pass
        await asyncio.sleep(interval)


def start_background_poll(interval: int = 30):
    """Call once at backend startup to begin background health polling."""
    global _poll_task
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            _poll_task = loop.create_task(_poll_loop(interval))
    except Exception:
        pass
