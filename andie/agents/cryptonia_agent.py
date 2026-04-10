import asyncio
import subprocess
from andie.agents.base_agent import Agent

class CryptoniaAgent(Agent):
    def __init__(self, config_path='services/cryptonia/config.yaml', dry_run=True, interval=300, **kwargs):
        super().__init__(name="cryptonia_agent", **kwargs)
        self.config_path = config_path
        self.dry_run = dry_run
        self.interval = interval
        self.process = None

    async def run(self):
        self.running = True
        while self.running:
            await self.start_bot()
            await asyncio.sleep(self.interval)

    async def start_bot(self):
        cmd = [
            'python3', 'services/cryptonia/main.py',
            '--config', self.config_path,
            '--interval', str(self.interval)
        ]
        if self.dry_run:
            cmd.append('--dry-run')
        else:
            cmd.append('--live')
        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            await asyncio.sleep(5)  # Let it start and run briefly
            # Optionally, collect output or check health
            await self.report({"cryptonia_status": "started", "pid": self.process.pid})
        except Exception as e:
            await self.report({"cryptonia_status": "error", "error": str(e)})

    async def stop_bot(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            await self.report({"cryptonia_status": "stopped"})
