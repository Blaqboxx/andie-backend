from __future__ import annotations

import asyncio
from typing import Any, Dict, Set

from fastapi import WebSocket


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event: Dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)

        disconnected: list[WebSocket] = []
        for websocket in subscribers:
            try:
                await websocket.send_json(event)
            except Exception:
                disconnected.append(websocket)

        if disconnected:
            async with self._lock:
                for websocket in disconnected:
                    self._subscribers.discard(websocket)

    async def subscribe(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers.add(websocket)

    async def unsubscribe(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers.discard(websocket)


event_bus = EventBus()