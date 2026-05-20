"""
observation/loop.py — Continuous background observation loop.

Runs all diagnostic domains on a schedule, caches the latest snapshot,
maintains a ring buffer of history, and fires state-change events when
a domain's health status transitions.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque

log = logging.getLogger(__name__)

# ── State storage ─────────────────────────────────────────────────────────────

@dataclass
class DomainSnapshot:
    domain: str
    status: str          # healthy | degraded | failed | unknown
    checks: list
    elapsed_ms: int
    captured_at: float   # monotonic timestamp


@dataclass
class ObservationSnapshot:
    overall: str
    domains: dict[str, DomainSnapshot]
    captured_at: float
    wall_time: str       # ISO-ish for display


# Ring buffer — last 60 snapshots (≈30 min @ 30s interval)
_history: Deque[ObservationSnapshot] = deque(maxlen=60)
_latest: ObservationSnapshot | None = None
_prev_domain_statuses: dict[str, str] = {}

# State-change event subscribers
_subscribers: list[Callable] = []

# Background task handle
_task: asyncio.Task | None = None
_running = False


# ── Public API ────────────────────────────────────────────────────────────────

def get_latest() -> ObservationSnapshot | None:
    return _latest


def get_history(n: int = 20) -> list[ObservationSnapshot]:
    snaps = list(_history)
    return snaps[-n:] if n < len(snaps) else snaps


def subscribe(fn: Callable) -> None:
    """Register a callback invoked on every state-change event."""
    _subscribers.append(fn)


def start(interval: int = 30) -> None:
    """Start the background observation loop (idempotent)."""
    global _task, _running
    if _running:
        return
    _running = True
    loop = asyncio.get_event_loop()
    _task = loop.create_task(_run_loop(interval))
    log.info("Observation loop started (interval=%ds)", interval)


def stop() -> None:
    global _task, _running
    _running = False
    if _task:
        _task.cancel()
        _task = None


# ── Internal ──────────────────────────────────────────────────────────────────

async def _run_loop(interval: int) -> None:
    from andie_backend.andie.diagnostics.probe_runner import run_all
    import datetime

    # Initial run immediately on startup
    await _collect(run_all)

    while _running:
        await asyncio.sleep(interval)
        if not _running:
            break
        try:
            await _collect(run_all)
        except Exception:
            log.exception("Observation loop iteration failed")


async def _collect(run_all_fn) -> None:
    global _latest, _prev_domain_statuses
    import datetime

    result = await run_all_fn()
    wall = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    now = time.monotonic()

    domains = {}
    for domain, data in result.get("domains", {}).items():
        domains[domain] = DomainSnapshot(
            domain=domain,
            status=data.get("status", "unknown"),
            checks=data.get("checks", []),
            elapsed_ms=data.get("elapsed_ms", 0),
            captured_at=now,
        )

    snap = ObservationSnapshot(
        overall=result.get("status", "unknown"),
        domains=domains,
        captured_at=now,
        wall_time=wall,
    )

    _latest = snap
    _history.append(snap)

    # Fire state-change events
    for domain, ds in domains.items():
        prev = _prev_domain_statuses.get(domain)
        if prev is not None and prev != ds.status:
            _fire_event("domain_status_change", {
                "domain": domain,
                "from": prev,
                "to": ds.status,
                "wall_time": wall,
            })
        _prev_domain_statuses[domain] = ds.status


def _fire_event(event_type: str, payload: dict) -> None:
    log.info("Observation event: %s %s", event_type, payload)
    for fn in _subscribers:
        try:
            fn(event_type, payload)
        except Exception:
            log.exception("Observation subscriber error")
