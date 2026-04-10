import asyncio
from andie.core.orchestrator import Orchestrator
from andie.agents.health_agent import HealthAgent
from andie.agents.process_agent import ProcessAgent
from andie.agents.recovery_agent import RecoveryAgent
from andie.agents.cryptonia_agent import CryptoniaAgent

async def main():
    orchestrator = Orchestrator()
    health_agent = HealthAgent()
    process_agent = ProcessAgent()
    recovery_agent = RecoveryAgent()
    orchestrator.register_agent(health_agent)
    orchestrator.register_agent(process_agent)
    orchestrator.register_agent(recovery_agent)
    # Register Cryptonia agent
    cryptonia_agent = CryptoniaAgent()
    orchestrator.register_agent(cryptonia_agent)
    print("[ANDIE] Orchestration started. Agents running...")
    await orchestrator.run_all()

if __name__ == "__main__":
    asyncio.run(main())
