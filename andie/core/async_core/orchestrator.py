import asyncio
from .task_queue import AsyncTaskQueue
from typing import Callable, Coroutine, Any

class AsyncOrchestrator:
    def __init__(self, max_concurrent: int = 3):
        self.task_queue = AsyncTaskQueue()
        self.max_concurrent = max_concurrent
        self.event_handlers = {}
        self.running = False

    def on_event(self, event_type: str, handler: Callable[[Any], Coroutine]):
        self.event_handlers[event_type] = handler

    async def trigger_event(self, event_type: str, payload: Any):
        handler = self.event_handlers.get(event_type)
        if handler:
            await self.task_queue.put(priority=1, coro=handler(payload))

    async def add_task(self, coro: Coroutine, priority: int = 1):
        await self.task_queue.put(priority, coro)

    async def run(self):
        self.running = True
        await self.task_queue.run(self.max_concurrent)

# Example agent task
async def example_agent_task(payload):
    print(f"Agent running with payload: {payload}")
    await asyncio.sleep(1)
    print(f"Agent finished: {payload}")

# Example event handler
async def on_user_request(payload):
    print(f"Handling user request: {payload}")
    await asyncio.sleep(0.5)

# Usage example (to be removed in integration):
# orchestrator = AsyncOrchestrator()
# orchestrator.on_event('user_request', on_user_request)
# asyncio.run(orchestrator.trigger_event('user_request', {'data': 123}))
# asyncio.run(orchestrator.run())
