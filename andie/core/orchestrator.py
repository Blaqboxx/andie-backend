import asyncio
from typing import Dict, Any, List
import time
from andie.core.decision_engine import compute_health_score, classify

class Orchestrator:
    def __init__(self):
        self.agents: List[Any] = []
        self.reports = []
        self.last_recovery_time = 0
        self.COOLDOWN = 60  # seconds
        self.system_state = {"status": "healthy"}
        self.recovery_failures = 0
        self.max_retries = 3

    def register_agent(self, agent):
        agent.set_orchestrator(self)
        self.agents.append(agent)

    async def receive_report(self, agent_name: str, data: Dict[str, Any]):
        # Compose health score and state if all signals present
        cpu = data.get("cpu")
        memory = data.get("memory")
        llm = data.get("llm_engine_active")
        health_score = None
        state = None
        if cpu is not None and memory is not None and llm is not None:
            health_score = compute_health_score(cpu, memory, llm)
            state = classify(health_score)
            log_line = f"[STATE] {state.upper()} | Score: {health_score} | CPU: {cpu:.1f}% | MEM: {memory:.1f}% | LLM: {'ACTIVE' if llm else 'INACTIVE'}"
            if state == "critical":
                log_line = "🚨 " + log_line
            elif state == "degraded":
                log_line = "⚠️  " + log_line
            else:
                log_line = "✅ " + log_line
            print(log_line)
        else:
            print(f"[ORCHESTRATOR] Report from {agent_name}: {data}")
        self.reports.append((agent_name, data))
        await self.evaluate(agent_name, data, health_score, state)

    def can_recover(self):
        return time.time() - self.last_recovery_time > self.COOLDOWN

    async def evaluate(self, agent_name: str, data: Dict[str, Any], health_score=None, state=None):
        # Use composite state for decisions
        cpu = data.get("cpu", 0)
        if state is not None:
            if state == "critical":
                if self.system_state["status"] != "recovering" and self.can_recover():
                    print("[ORCHESTRATOR] CRITICAL state detected! Triggering recovery...")
                    self.system_state["status"] = "recovering"
                    self.last_recovery_time = time.time()
                    for agent in self.agents:
                        if getattr(agent, "name", "") == "recovery_agent":
                            await agent.recover_service()
                    self.recovery_failures += 1
