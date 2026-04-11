import asyncio
from typing import Callable, Coroutine, Any

class EventSystem:
    def __init__(self):
        self.listeners = {}

    def register(self, event_type: str, handler: Callable[[Any], Coroutine]):
        if event_type not in self.listeners:
            self.listeners[event_type] = []
        self.listeners[event_type].append(handler)

    async def emit(self, event_type: str, payload: Any):
        handlers = self.listeners.get(event_type, [])
        await asyncio.gather(*(handler(payload) for handler in handlers))
