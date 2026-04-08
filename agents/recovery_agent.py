import asyncio
import os
from agents.base_agent import Agent

class RecoveryAgent(Agent):
    def __init__(self, **kwargs):
        super().__init__(name="recovery_agent", **kwargs)

    async def run(self):
        # Recovery agent is event-driven, not looped
        await asyncio.sleep(3600)

    async def recover_service(self):
        print("[RECOVERY AGENT] Restarting Uvicorn server...")
        os.system("pkill -f 'uvicorn main:app'")
        await asyncio.sleep(1)
        os.system("nohup uvicorn main:app --host 0.0.0.0 --port 8000 &")
        print("[RECOVERY AGENT] Restart command issued.")
