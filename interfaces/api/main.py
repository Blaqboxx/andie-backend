from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any, Dict
import importlib
import requests
import sys
from pathlib import Path
import os

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
    context: str | None = None

class AgentRequest(BaseModel):
    input: Any = None
    params: Dict[str, Any] = {}

# --- Orchestrator endpoint ---
@app.post("/orchestrator/run")
def run_orchestrator(req: OrchestratorRequest):
    try:
        orchestrator = importlib.import_module("orchestrator")
        result = orchestrator.run_orchestrator(req.task, context=req.context)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Agent endpoint (dynamic) ---
@app.post("/agent/{agent_name}")
def run_agent(agent_name: str, req: AgentRequest):
    try:
        # Try ANDIE agents first
        try:
            agent_mod = importlib.import_module(agent_name)
        except ImportError:
            # Try Malk agents
            sys.path.insert(0, malk_agents_path)
            agent_mod = importlib.import_module(agent_name)

        # Build structured LLM input contract
        llm_input = {
            "prompt": req.input if isinstance(req.input, str) else str(req.input),
            "system": req.params.get("system", "You are an AI agent executing a task."),
            "context": req.params.get("context", ""),
            "metadata": {"agent": agent_name, **req.params.get("metadata", {})}
        }

        if hasattr(agent_mod, "run_agent"):
            result = agent_mod.run_agent(llm_input)
        elif hasattr(agent_mod, "main"):
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
