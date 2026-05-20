"""
probe_runner.py — Execute named probe sets and return structured diagnostic results.
"""
from __future__ import annotations
import asyncio
import time
from typing import Literal

ProbeStatus = Literal["healthy", "degraded", "failed", "unreachable", "unknown"]

DOMAIN_MAP = {
    "containers": "andie_backend.andie.diagnostics.probes.containers",
    "network":    "andie_backend.andie.diagnostics.probes.network",
    "gpu":        "andie_backend.andie.diagnostics.probes.gpu",
    "storage":    "andie_backend.andie.diagnostics.probes.storage",
    "models":     "andie_backend.andie.diagnostics.probes.models",
    "workers":    "andie_backend.andie.diagnostics.probes.workers",
}

ALL_DOMAINS = list(DOMAIN_MAP.keys())


async def run_domain(domain: str) -> dict:
    """Run all probes in a single domain. Returns {domain, status, checks, elapsed_ms}."""
    mod_path = DOMAIN_MAP.get(domain)
    if not mod_path:
        return {"domain": domain, "status": "unknown", "checks": [], "error": "unknown domain"}

    t0 = time.monotonic()
    try:
        import importlib
        mod = importlib.import_module(mod_path)
        checks = await mod.run()
    except Exception as e:
        return {"domain": domain, "status": "failed", "checks": [], "error": str(e),
                "elapsed_ms": round((time.monotonic() - t0) * 1000)}

    elapsed = round((time.monotonic() - t0) * 1000)

    # Roll up status: any failed → failed, any degraded → degraded, else healthy
    statuses = [c.get("status", "unknown") for c in checks]
    if "failed" in statuses or "unreachable" in statuses:
        rolled = "failed"
    elif "degraded" in statuses or "unknown" in statuses:
        rolled = "degraded"
    else:
        rolled = "healthy"

    return {"domain": domain, "status": rolled, "checks": checks, "elapsed_ms": elapsed}


async def run_all() -> dict:
    """Run all domains in parallel. Returns {status, domains, elapsed_ms}."""
    t0 = time.monotonic()
    results = await asyncio.gather(*[run_domain(d) for d in ALL_DOMAINS])
    elapsed = round((time.monotonic() - t0) * 1000)

    statuses = [r["status"] for r in results]
    if "failed" in statuses:
        overall = "failed"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "domains": {r["domain"]: r for r in results},
        "elapsed_ms": elapsed,
    }
