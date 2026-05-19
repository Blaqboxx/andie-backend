import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AutonomousLoop:
    """
    Continuously calls orchestrator.handle() on a fixed interval.
    Designed to be launched as a background asyncio task at startup.
    """

    DEFAULT_TICK_MESSAGE = "autonomy:tick"
    DEFAULT_INTERVAL = 30.0  # seconds

    def __init__(self, orchestrator, interval: float = DEFAULT_INTERVAL):
        self.orchestrator = orchestrator
        self.interval = interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def tick(self):
        """Single autonomous cycle — delegates to orchestrator."""
        try:
            result = await self.orchestrator.handle(self.DEFAULT_TICK_MESSAGE)
            logger.debug("AutonomousLoop tick: %s", result)
        except Exception as exc:
            logger.error("AutonomousLoop tick error: %s", exc, exc_info=True)

    async def _loop(self):
        logger.info("AutonomousLoop started (interval=%.1fs)", self.interval)
        while self._running:
            await self.tick()
            await asyncio.sleep(self.interval)
        logger.info("AutonomousLoop stopped")

    async def start(self):
        """Start the loop as a background asyncio task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="autonomy-loop")

    def stop(self):
        """Signal the loop to stop after the current tick."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
