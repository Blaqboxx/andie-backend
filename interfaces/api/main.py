
import sys
import importlib
import requests
import os
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any, Dict
from pathlib import Path

# --- Import new async orchestrator ---
from andie_core.async_core.orchestrator import AsyncOrchestrator
from andie_core.async_core.task_queue import AsyncTaskQueue
from andie_core.async_core.event_system import EventSystem

# --- Dynamic import helpers ---
andie_core_path = str(Path(__file__).resolve().parent.parent.parent / "andie" / "core")
if andie_core_path not in sys.path:
    sys.path.insert(0, andie_core_path)

agents_path = str(Path(__file__).resolve().parent.parent.parent / "andie" / "agents")
if agents_path not in sys.path:
    sys.path.insert(0, agents_path)

malk_agents_path = str(Path(__file__).resolve().parent.parent.parent / "services" / "malk" / "agents")
if malk_agents_path not in sys.path:
    sys.path.insert(0, malk_agents_path)

# --- FastAPI app ---
app = FastAPI()

# --- Models ---
class OrchestratorRequest(BaseModel):
    task: str
    params: Dict[str, Any] = {}

class AgentRequest(BaseModel):
    input: Any = None
    params: Dict[str, Any] = {}



# --- Async orchestrator and event system instances ---
async_orchestrator = AsyncOrchestrator()
event_system = EventSystem()

# Example event handler: queues a task in orchestrator
async def handle_event_task(payload):
    async def agent_task():
        agent_name = payload.get("agent")
        params = payload.get("params", {})
        agent_mod = importlib.import_module(agent_name)
        if hasattr(agent_mod, "run_agent"):
            if asyncio.iscoroutinefunction(agent_mod.run_agent):
                return await agent_mod.run_agent(params)
            else:
                return agent_mod.run_agent(params)
        elif hasattr(agent_mod, "main"):
            if asyncio.iscoroutinefunction(agent_mod.main):
                return await agent_mod.main(params)
            else:
                return agent_mod.main(params)
        else:
            raise Exception(f"No run_agent or main() in {agent_name}")
    await async_orchestrator.add_task(agent_task(), priority=payload.get("priority", 1))

# Register the handler for a generic event type
event_system.register("agent_task", handle_event_task)

# --- Async Orchestrator endpoint ---
@app.post("/orchestrator/run")
async def run_async_orchestrator(req: OrchestratorRequest):
    try:
        # Wrap the task as a coroutine
        async def agent_task():
            # Dynamically import agent module if needed
            agent_mod = importlib.import_module(req.task)
            if hasattr(agent_mod, "run_agent"):
                if asyncio.iscoroutinefunction(agent_mod.run_agent):
                    return await agent_mod.run_agent(req.params)
                else:
                    return agent_mod.run_agent(req.params)
            elif hasattr(agent_mod, "main"):
                if asyncio.iscoroutinefunction(agent_mod.main):
                    return await agent_mod.main(req.params)
                else:
                    return agent_mod.main(req.params)
            else:
                raise Exception(f"No run_agent or main() in {req.task}")

        await async_orchestrator.add_task(agent_task(), priority=req.params.get("priority", 1))
        return {"status": "queued"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Async Agent endpoint (dynamic) ---
@app.post("/agent/{agent_name}")
async def run_agent_async(agent_name: str, req: AgentRequest):
    try:
        # Try ANDIE agents first
        try:
            agent_mod = importlib.import_module(agent_name)
        except ImportError:
            sys.path.insert(0, malk_agents_path)
            agent_mod = importlib.import_module(agent_name)

        llm_input = {
            "prompt": req.input if isinstance(req.input, str) else str(req.input),
            "system": req.params.get("system", "You are an AI agent executing a task."),
            "context": req.params.get("context", ""),
            "metadata": {"agent": agent_name, **req.params.get("metadata", {})}
        }

        # Run agent as async if possible
        if hasattr(agent_mod, "run_agent"):
            if asyncio.iscoroutinefunction(agent_mod.run_agent):
                result = await agent_mod.run_agent(llm_input)
            else:
                result = agent_mod.run_agent(llm_input)
        elif hasattr(agent_mod, "main"):
            if asyncio.iscoroutinefunction(agent_mod.main):
                result = await agent_mod.main(llm_input)
            else:
                result = agent_mod.main(llm_input)
        else:
            raise Exception(f"No run_agent or main() in {agent_name}")
        return {"status": "executed", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Health endpoint ---
@app.get("/health")
def health():
    return {"status": "ok"}

# --- System status endpoint ---
# --- System status endpoint ---
@app.get("/system/status")
def system_status():
    # Example: ping memory API, check orchestrator, etc.
    try:
        mem_status = requests.get("http://localhost:8000/health").json()
    except Exception:
        mem_status = {"status": "unreachable"}
    return {
        "orchestrator": "ready",
        "memory": mem_status.get("status", "unknown"),
        "agents": "ready"
    }


# --- Event trigger endpoint ---
class EventTriggerRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any] = {}

@app.post("/event/trigger")
async def trigger_event(req: EventTriggerRequest):
    try:
        await event_system.emit(req.event_type, req.payload)
        return {"status": "event_triggered"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
