import asyncio
import heapq
from typing import Any, Callable, Coroutine, List, Tuple

class PrioritizedTask:
    def __init__(self, priority: int, coro: Coroutine, task_id: str = None):
        self.priority = priority
        self.coro = coro
        self.task_id = task_id or str(id(self))
    def __lt__(self, other):
        return self.priority < other.priority

class AsyncTaskQueue:
    def __init__(self):
        self._queue: List[Tuple[int, int, PrioritizedTask]] = []
        self._counter = 0
        self._lock = asyncio.Lock()

    async def put(self, priority: int, coro: Coroutine, task_id: str = None):
        async with self._lock:
            heapq.heappush(self._queue, (priority, self._counter, PrioritizedTask(priority, coro, task_id)))
            self._counter += 1

    async def get(self) -> PrioritizedTask:
        async with self._lock:
            if not self._queue:
                return None
            return heapq.heappop(self._queue)[2]

    async def run(self, max_concurrent: int = 3):
        sem = asyncio.Semaphore(max_concurrent)
        async def worker():
            while True:
                task = await self.get()
                if not task:
                    await asyncio.sleep(0.1)
                    continue
                async with sem:
                    try:
                        await task.coro
                    except Exception as e:
                        print(f"Task {task.task_id} failed: {e}")
        workers = [asyncio.create_task(worker()) for _ in range(max_concurrent)]
        await asyncio.gather(*workers)
