import asyncio
from typing import Callable, Coroutine, Any

class FeedbackLoop:
    def __init__(self, evaluate_fn: Callable[[Any], bool], retry_limit: int = 2):
        self.evaluate_fn = evaluate_fn
        self.retry_limit = retry_limit

    async def run_with_feedback(self, task_coro: Callable[[], Coroutine], *args, **kwargs):
        attempts = 0
        while attempts <= self.retry_limit:
            result = await task_coro(*args, **kwargs)
            if self.evaluate_fn(result):
                return result
            attempts += 1
            print(f"Retrying task, attempt {attempts}")
        print("Task failed after retries.")
        return None
