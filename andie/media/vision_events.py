"""
andie/media/vision_events.py
Ring buffer of vision events. Fed by OCR results + future CV pipeline.
Hooks into the observation loop as a synthetic domain.
"""
from __future__ import annotations
import time
from collections import deque
from typing import Optional

_MAX_EVENTS = 200
_event_buffer: deque[dict] = deque(maxlen=_MAX_EVENTS)
_subscribers: list = []

def push_events(events: list[dict], source: str = "ocr") -> None:
    """Add detected events into the ring buffer and notify subscribers."""
    now = time.time()
    for ev in events:
        record = {
            **ev,
            "source": source,
            "ts": now,
        }
        _event_buffer.appendleft(record)
        for fn in _subscribers:
            try:
                fn(record)
            except Exception:
                pass

def get_events(limit: int = 50, severity: Optional[str] = None) -> list[dict]:
    events = list(_event_buffer)
    if severity:
        events = [e for e in events if e.get("severity") == severity]
    return events[:limit]

def subscribe(fn) -> None:
    _subscribers.append(fn)

def clear() -> None:
    _event_buffer.clear()

def summary() -> dict:
    events = list(_event_buffer)
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for e in events:
        by_type[e.get("type", "unknown")] = by_type.get(e.get("type", "unknown"), 0) + 1
        by_severity[e.get("severity", "info")] = by_severity.get(e.get("severity", "info"), 0) + 1
    return {
        "total": len(events),
        "by_type": by_type,
        "by_severity": by_severity,
        "latest": events[0] if events else None,
    }

# ── Observation loop integration ─────────────────────────────────────────────
# Called by the observation loop's probe runner to surface media domain status.

async def run() -> list[dict]:
    """Probe function compatible with probe_runner DOMAIN_MAP."""
    evs = list(_event_buffer)
    recent = [e for e in evs if time.time() - e.get("ts", 0) < 300]  # last 5 min

    high = [e for e in recent if e.get("severity") == "high"]
    medium = [e for e in recent if e.get("severity") == "medium"]

    status = "healthy"
    if high:
        status = "degraded"
    elif medium:
        status = "advisory"

    return [{
        "check": "vision-events",
        "status": status,
        "detail": (
            f"{len(recent)} events in last 5min"
            + (f" — {len(high)} errors" if high else "")
        ),
    }]
