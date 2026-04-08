import asyncio
import psutil
from agents.base_agent import Agent

class ProcessAgent(Agent):
    def __init__(self, process_name="uvicorn", interval=5, **kwargs):
        super().__init__(name="process_agent", **kwargs)
        self.process_name = process_name
        self.interval = interval

    async def run(self):
        self.running = True
        while self.running:
            found = any(self.process_name in p.name() for p in psutil.process_iter())
            await self.report({"process_running": found})
            await asyncio.sleep(self.interval)
