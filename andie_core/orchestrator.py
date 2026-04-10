from andie.brain.llm_router import call_llm
from andie.memory.memory_service import MemoryService

memory = MemoryService()

def run_orchestrator(task: str, context: str = None):
    # 1. Retrieve memory context (optional, can combine with user context)
    mem_context = memory.query_memory(task)
    combined_context = context if context else str(mem_context)

    # 2. Build LLM input
    response = call_llm(
        prompt=task,
        system="You are ANDIE orchestrator. Plan and execute tasks.",
        context=combined_context
    )

    # 3. Store result
    memory.store_memory(response, {"task": task, "context": combined_context})

    # 4. Return result
    return {
        "task": task,
        "response": response,
        "context_used": combined_context
    }

import asyncio
from typing import Dict, Any, List
import time
from andie_core.decision_engine import compute_health_score, classify

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
                    if self.recovery_failures > self.max_retries:
                        print("[ORCHESTRATOR] Recovery failed too many times. Escalating!")
                else:
                    print("[ORCHESTRATOR] CRITICAL state, but in cooldown or already recovering.")
            elif state == "healthy" and self.system_state["status"] == "recovering":
                print("[ORCHESTRATOR] System recovered. Marking as healthy.")
                self.system_state["status"] = "healthy"
                self.recovery_failures = 0
            elif state == "degraded":
                print("[ORCHESTRATOR] System is degraded. Monitoring closely.")
        else:
            # Fallback to old logic if not enough data
            if cpu > 85:
                if self.system_state["status"] != "recovering" and self.can_recover():
                    print("[ORCHESTRATOR] High CPU detected! Triggering recovery...")
                    self.system_state["status"] = "recovering"
                    self.last_recovery_time = time.time()
                    for agent in self.agents:
                        if getattr(agent, "name", "") == "recovery_agent":
                            await agent.recover_service()
                    self.recovery_failures += 1
                    if self.recovery_failures > self.max_retries:
                        print("[ORCHESTRATOR] Recovery failed too many times. Escalating!")
                else:
                    print("[ORCHESTRATOR] High CPU detected, but in cooldown or already recovering.")
            elif cpu < 70 and self.system_state["status"] == "recovering":
                print("[ORCHESTRATOR] System recovered. Marking as healthy.")
                self.system_state["status"] = "healthy"
                self.recovery_failures = 0

    async def run_all(self):
        await asyncio.gather(*(agent.run() for agent in self.agents))
