from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from collections import deque
from typing import Any, Dict, Set


@dataclass(frozen=True)
class StreamSubscriber:
    queue: asyncio.Queue[Dict[str, Any]]
    loop: asyncio.AbstractEventLoop


class StreamEventBus:
    def __init__(self) -> None:
        self._subscribers: Set[StreamSubscriber] = set()
        self._lock = asyncio.Lock()
        self._history = deque(maxlen=int(os.environ.get("ANDIE_STREAM_EVENT_HISTORY_LIMIT", "200")))
        self._history_lock = threading.Lock()

    async def subscribe(self) -> asyncio.Queue[Dict[str, Any]]:
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        async with self._lock:
            self._subscribers.add(StreamSubscriber(queue=queue, loop=loop))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[Dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers = {subscriber for subscriber in self._subscribers if subscriber.queue is not queue}

    async def emit(self, event: Dict[str, Any]) -> None:
        with self._history_lock:
            self._history.append(dict(event))

        async with self._lock:
            subscribers = list(self._subscribers)

        disconnected: list[StreamSubscriber] = []
        for subscriber in subscribers:
            if subscriber.loop.is_closed():
                disconnected.append(subscriber)
                continue
            subscriber.loop.call_soon_threadsafe(subscriber.queue.put_nowait, event)

        if disconnected:
            async with self._lock:
                for subscriber in disconnected:
                    self._subscribers.discard(subscriber)

    def recent_events(self, limit: int = 25) -> list[Dict[str, Any]]:
        with self._history_lock:
            items = list(self._history)
        if limit <= 0:
            return []
        return items[-limit:]


stream_event_bus = StreamEventBus()


async def subscribe() -> asyncio.Queue[Dict[str, Any]]:
    return await stream_event_bus.subscribe()


async def unsubscribe(queue: asyncio.Queue[Dict[str, Any]]) -> None:
    await stream_event_bus.unsubscribe(queue)


async def emit_event(event: Dict[str, Any]) -> None:
    await stream_event_bus.emit(event)


def recent_events(limit: int = 25) -> list[Dict[str, Any]]:
    return stream_event_bus.recent_events(limit)