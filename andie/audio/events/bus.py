"""
andie/audio/events/bus.py
Ring-buffer audio event bus — mirrors vision_events pattern.

Events are pushed by ASR, wake word detector, and streaming pipeline.
Subscribers receive every event synchronously (fire-and-forget).

Probe interface:
  async def run() -> list[dict]   ← used by probe_runner DOMAIN_MAP
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable, Optional

# ── Ring buffer ──────────────────────────────────────────────────────────────
_MAXLEN = 300
_ring: deque[dict] = deque(maxlen=_MAXLEN)
_subscribers: list[Callable[[dict], None]] = []

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

def push_event(event: dict, source: str = "unknown") -> None:
    """Push a single audio event onto the ring buffer and notify subscribers."""
    entry = {
        "ts": time.time(),
        "source": source,
        "type":     event.get("type", "unknown"),
        "value":    event.get("value", ""),
        "severity": event.get("severity", "info"),
        "meta":     event.get("meta", {}),
    }
    _ring.append(entry)
    for fn in _subscribers:
        try:
            fn(entry)
        except Exception:
            pass


def push_events(events: list[dict], source: str = "unknown") -> None:
    for ev in events:
        push_event(ev, source=source)


def get_events(
    limit: int = 50,
    severity: Optional[str] = None,
    since: Optional[float] = None,
) -> list[dict]:
    items = list(_ring)
    if severity:
        items = [e for e in items if e["severity"] == severity]
    if since:
        items = [e for e in items if e["ts"] >= since]
    return items[-limit:]


def subscribe(fn: Callable[[dict], None]) -> None:
    _subscribers.append(fn)


def unsubscribe(fn: Callable[[dict], None]) -> None:
    if fn in _subscribers:
        _subscribers.remove(fn)


def summary() -> dict:
    items = list(_ring)
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for e in items:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        by_severity[e["severity"]] = by_severity.get(e["severity"], 0) + 1
    latest = items[-1] if items else None
    return {
        "total": len(items),
        "by_type": by_type,
        "by_severity": by_severity,
        "latest": latest,
    }


def clear() -> None:
    _ring.clear()


# ── Probe interface ──────────────────────────────────────────────────────────
async def run() -> list[dict]:
    """Observation loop probe — reports audio event bus health."""
    items = list(_ring)
    five_min_ago = time.time() - 300
    recent = [e for e in items if e["ts"] >= five_min_ago]
    high = [e for e in recent if e["severity"] in ("critical", "high")]

    checks = [{
        "check": "audio-event-bus",
        "status": "degraded" if high else "healthy",
        "detail": (
            f"{len(recent)} events in last 5min"
            + (f", {len(high)} high-severity" if high else "")
        ),
    }]
    return checks
