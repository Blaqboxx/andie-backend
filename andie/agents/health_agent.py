import asyncio
import psutil
from andie.agents.base_agent import Agent

class HealthAgent(Agent):
    def __init__(self, interval=5, **kwargs):
        super().__init__(name="health_agent", **kwargs)
        self.interval = interval

    async def run(self):
        import importlib
        self.running = True
        while self.running:
            cpu = psutil.cpu_percent()
            mem = psutil.virtual_memory().percent
            # Check LLM engine status
            llm_status = False
            try:
                llm_engine = importlib.import_module('andie.brain.llm_engine')
                llm_status = True
            except Exception as e:
                llm_status = False
            await self.report({
                "cpu": cpu,
                "memory": mem,
                "llm_engine_active": llm_status
            })
            await asyncio.sleep(self.interval)
