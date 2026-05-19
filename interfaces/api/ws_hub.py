"""
WebSocketHub — central broadcaster for real-time agent events.

Usage:
    from andie_backend.interfaces.api.ws_hub import ws_hub

    # In a FastAPI websocket handler:
    await ws_hub.connect(websocket)

    # From anywhere to broadcast:
    await ws_hub.broadcast({"type": "agent.response", "agent": "crypto_agent", ...})
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketHub:
    """Thread-safe hub that broadcasts JSON messages to all connected WebSocket clients."""

    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info("WS client connected  (total=%d)", len(self._connections))
        # Send a welcome frame so the client knows the channel is live
        await self._send_one(websocket, {
            "type": "connection.ready",
            "message": "ANDIE WebSocket stream connected",
        })

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("WS client disconnected (total=%d)", len(self._connections))

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Send a JSON message to every connected client, dropping dead sockets silently."""
        async with self._lock:
            targets = list(self._connections)

        dead: list[WebSocket] = []
        for ws in targets:
            if not await self._send_one(ws, message):
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    async def _send_one(self, websocket: WebSocket, message: Dict[str, Any]) -> bool:
        try:
            await websocket.send_json(message)
            return True
        except Exception:
            return False

    @property
    def client_count(self) -> int:
        return len(self._connections)

    # Compatibility shim — old code calls websocket_event_bus.publish(event)
    async def publish(self, event: Dict[str, Any]) -> None:
        await self.broadcast(event)

    # Compatibility shim — old code calls websocket_event_bus.subscribe(ws)
    async def subscribe(self, websocket: WebSocket) -> None:
        await self.connect(websocket)

    # Compatibility shim — old code calls websocket_event_bus.unsubscribe(ws)
    async def unsubscribe(self, websocket: WebSocket) -> None:
        await self.disconnect(websocket)


# Module-level singleton — import this everywhere
ws_hub = WebSocketHub()
