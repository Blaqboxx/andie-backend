"""Probe: container / service endpoint health."""
from __future__ import annotations
import httpx

SERVICES = [
    {"name": "andie-backend",  "url": "http://localhost:8010/health",      "critical": True},
    {"name": "andie-ui",       "url": "http://andie-ui:3000/",             "critical": False},
    {"name": "ollama",         "url": "http://192.168.50.9:11434/api/tags","critical": True},
    {"name": "redis",          "url": None,                                 "critical": False},
]


async def _check_http(name: str, url: str, critical: bool) -> dict:
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(url)
        if r.status_code < 500:
            return {"check": name, "status": "healthy", "detail": f"HTTP {r.status_code}"}
        return {"check": name, "status": "degraded", "detail": f"HTTP {r.status_code}"}
    except httpx.ConnectError:
        status = "unreachable" if critical else "degraded"
        return {"check": name, "status": status, "detail": "connection refused"}
    except Exception as e:
        return {"check": name, "status": "degraded", "detail": str(e)[:120]}


async def run() -> list[dict]:
    checks = []
    for svc in SERVICES:
        if svc["url"] is None:
            checks.append({"check": svc["name"], "status": "unknown", "detail": "no probe configured"})
            continue
        checks.append(await _check_http(svc["name"], svc["url"], svc["critical"]))
    return checks
