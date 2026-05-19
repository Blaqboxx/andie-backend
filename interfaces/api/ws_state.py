"""
ws_state.py — lightweight WebSocket state broadcaster.

Call broadcast_state() from any async function to push an andie_state
event to every connected WebSocket subscriber.

Call schedule_broadcast() from synchronous code or fire-and-forget inside
async functions where you don't want to await.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from event_bus import event_bus as _bus
    _BUS_AVAILABLE = True
except ImportError:
    _BUS_AVAILABLE = False
    _bus = None  # type: ignore[assignment]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_state(state: str) -> str:
    alias = {
        "agent_idle": "idle",
        "agent_listening": "listening",
        "agent_thinking": "thinking",
        "agent_speaking": "speaking",
        "agent_improving": "improving",
        "agent_improved": "improved",
        "agent_error": "error",
        "self_build_start": "improving",
        "self_build_done": "improved",
        "self_review_start": "thinking",
        "self_review_done": "idle",
        "improve_start": "improving",
        "improve_done": "improved",
    }
    return alias.get(state, state)


async def broadcast_state(
    state: str,
    detail: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Broadcast an ANDIE visual state to all WebSocket subscribers.

    Valid states: idle | listening | thinking | speaking | improving | improved | error
    """
    if not _BUS_AVAILABLE or _bus is None:
        return
    normalized = _normalize_state(str(state))
    await _bus.publish(
        {
            "type": "andie_state",
            "state": normalized,
            "detail": detail,
            "meta": meta or {},
            "ts": _utc_now(),
        }
    )


def schedule_broadcast(
    state: str,
    detail: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Fire-and-forget broadcast.  Safe to call from sync or async context."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_state(state, detail, meta))
    except RuntimeError:
        pass
