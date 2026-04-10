import asyncio
from typing import Any, Dict

class Agent:
    def __init__(self, name: str, orchestrator=None):
        self.name = name
        self.orchestrator = orchestrator
        self.running = False

    async def run(self):
        """Override this in subclasses with agent's main loop."""
        raise NotImplementedError

    async def report(self, data: Dict[str, Any]):
        if self.orchestrator:
            await self.orchestrator.receive_report(self.name, data)

    def set_orchestrator(self, orchestrator):
        self.orchestrator = orchestrator
