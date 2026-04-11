
# ------------------------
# MEMORY QUERY ENDPOINT (REAL)
# ------------------------

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from andie.brain.llm_router import call_llm
import subprocess
import os
import signal
from threading import Lock
import asyncio
from agents.health_agent import HealthAgent

# --- LLM Client Setup ---
app = FastAPI()

from andie.memory.memory_service import MemoryService
memory_service = MemoryService()

@app.post("/memory/query")
def memory_query(data: dict):
    query = data.get("query", "")
    if not query:
        return {"results": []}
    # Use the real memory service
    result_obj = memory_service.query_memory(query)
    # Add IDs for frontend compatibility
    results = [
        {"id": i+1, "text": r["content"]} for i, r in enumerate(result_obj.get("results", []))
    ]
    return {"results": results}

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from andie.brain.llm_router import call_llm
import subprocess
import os
import signal
from threading import Lock
import asyncio
from agents.health_agent import HealthAgent


# --- LLM Client Setup ---
app = FastAPI()

# --- Uvicorn Entrypoint ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

# --- CORS for frontend dev ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- Health Agent Background Startup ---
@app.post("/orchestrator/run")
async def orchestrator_run(request: Request):
    data = await request.json()
    task = data.get("task", "")
    context = data.get("context", "")
    # Optionally use context in call_llm if needed
    result = call_llm(task)
    return {"response": result, "status": "ok"}
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Health Agent Status Store ---
health_status = {"status": "unknown"}

# Function for HealthAgent to update status
def update_health_status(data):
    global health_status
    health_status.update(data)

# --- Health Agent Background Startup ---
class FastAPIHealthAgent(HealthAgent):
    async def report(self, data):
        print("[HealthAgent] report called with:", data)
        update_health_status(data)
    async def run(self):
        print("[HealthAgent] run started")
        await super().run()

health_agent_instance = FastAPIHealthAgent()

@app.on_event("startup")
async def start_health_agent():
    loop = asyncio.get_event_loop()
    loop.create_task(health_agent_instance.run())

# --- Health Agent Status Store ---
health_status = {"status": "unknown"}

# Function for HealthAgent to update status
def update_health_status(data):
    global health_status
    health_status.update(data)


# --- Cryptonia Agent Process Management ---
cryptonia_process = None
cryptonia_status = "stopped"
cryptonia_lock = Lock()

def get_cryptonia_pid():
    global cryptonia_process
    if cryptonia_process and cryptonia_process.poll() is None:
        return cryptonia_process.pid
    return None

@app.post("/agents/cryptonia/start")
def start_cryptonia():
    global cryptonia_process, cryptonia_status
    with cryptonia_lock:
        if cryptonia_process and cryptonia_process.poll() is None:
            cryptonia_status = "running"
            return {"status": "already running", "pid": cryptonia_process.pid}
        try:
            cryptonia_process = subprocess.Popen([
                "python3", "Cryptonia/main.py", "--dry-run"
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            cryptonia_status = "running"
            return {"status": "started", "pid": cryptonia_process.pid}
        except Exception as e:
            cryptonia_status = "error"
            return {"status": "error", "error": str(e)}

@app.post("/agents/cryptonia/stop")
def stop_cryptonia():
    global cryptonia_process, cryptonia_status
    with cryptonia_lock:
        if cryptonia_process and cryptonia_process.poll() is None:
            try:
                os.kill(cryptonia_process.pid, signal.SIGTERM)
                cryptonia_status = "stopped"
                return {"status": "stopped"}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        else:
            cryptonia_status = "stopped"
            return {"status": "not running"}

@app.get("/agents/cryptonia/status")
def cryptonia_status_endpoint():
    global cryptonia_process, cryptonia_status
    if cryptonia_process and cryptonia_process.poll() is None:
        return {"status": "running", "pid": cryptonia_process.pid}
    return {"status": cryptonia_status}


## LLM client is now handled by andie.brain.llm_router.call_llm

# ------------------------
# SYSTEM STATUS
# ------------------------
@app.get("/system/status")
def system_status():
    return {
        "status": "online",
        "agents": ["NEXUS", "ORACLE", "HERALD", "CIPHER", "WRAITH"],
        "uptime": "active"
    }

# ------------------------
# AGENTS LIST
# ------------------------
@app.get("/agents")
def get_agents():
    return [
        {"name": "NEXUS", "role": "orchestrator"},
        {"name": "ORACLE", "role": "reasoning"},
        {"name": "HERALD", "role": "language"},
        {"name": "CIPHER", "role": "security"},
        {"name": "WRAITH", "role": "web"}
    ]

# ------------------------
# AGENT RUN (LLM CORE)
# ------------------------

@app.post("/agents/run")
def run_agent(data: dict):
    task = data.get("task", "")
    result = call_llm(task)
    return {
        "result": result,
        "status": "ok"
    }

# ------------------------
# TERMINAL (SECURED BASIC)
# ------------------------
DANGEROUS = ["rm -rf", "shutdown", "reboot", "mkfs", "dd if="]
def is_dangerous(cmd):
    return any(x in cmd.lower() for x in DANGEROUS)

@app.post("/terminal/run")
def run_terminal(data: dict):
    cmd = data.get("command", "")
    if is_dangerous(cmd):
        return {"error": "Blocked by Security Sentinel"}
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        return {
            "output": result.stdout,
            "error": result.stderr
        }
    except Exception as e:
        return {"error": str(e)}

# ------------------------
# TASKS (PLACEHOLDER)
# ------------------------
@app.get("/tasks")
def get_tasks():
    return [
        {"id": 1, "task": "System check", "status": "complete"},
        {"id": 2, "task": "Agent sync", "status": "running"}
    ]

# ------------------------
# SECURITY LOGS
# ------------------------
@app.get("/security/logs")
def security_logs():
    return [{"event": "No threats detected"}]

# ------------------------
# CHAT (MOBILE CHAT UI)
# ------------------------
@app.post("/chat")
def chat(data: dict):
    message = data.get("message", "")
    result = call_llm(message)
    return {
        "response": result,
        "status": "ok"
    }

@app.get("/agents/health/status")
def get_health_status():
    return health_status

# --- Health Check and Frontend Redirect ---








# ------------------------
# SYSTEM STATUS
# ------------------------
@app.get("/system/status")
def system_status():
    return {
        "status": "online",
        "agents": ["NEXUS", "ORACLE", "HERALD", "CIPHER", "WRAITH"],
        "uptime": "active"
    }

# ------------------------
# AGENTS LIST
# ------------------------
@app.get("/agents")
def get_agents():
    return [
        {"name": "NEXUS", "role": "orchestrator"},
        {"name": "ORACLE", "role": "reasoning"},
        {"name": "HERALD", "role": "language"},
        {"name": "CIPHER", "role": "security"},
        {"name": "WRAITH", "role": "web"}
    ]

# ------------------------
# AGENT RUN (LLM CORE)
# ------------------------




@app.post("/agents/run")
def run_agent(data: dict):
    # Canonicalize LLM input contract
    prompt = data.get("prompt") or data.get("input") or data.get("task") or ""
    llm_input = {
        "prompt": prompt,
        "system": data.get("system", "You are an AI agent executing a task."),
        "context": data.get("context", ""),
        "metadata": {"agent": data.get("agent", "unknown")}
    }

    agent_name = data.get("agent")
    if agent_name:
        try:
            import importlib
            agent_mod = importlib.import_module(f"andie.agents.{agent_name}")
            if hasattr(agent_mod, "run_agent"):
                result = agent_mod.run_agent(llm_input)
            elif hasattr(agent_mod, "main"):
                result = agent_mod.main(llm_input)
            else:
                result = f"No run_agent or main() in {agent_name}"
        except Exception as e:
            result = f"Agent import/run error: {str(e)}"
    else:
        # llm_input should be a string prompt, not a dict
        if isinstance(llm_input, dict) and "prompt" in llm_input:
            result = call_llm(llm_input["prompt"])
        else:
            result = call_llm(llm_input)

    return {
        "result": result,
        "status": "ok"
    }

# ------------------------
# TERMINAL (SECURED BASIC)
# ------------------------
DANGEROUS = ["rm -rf", "shutdown", "reboot", "mkfs", "dd if="]

def is_dangerous(cmd):
    return any(x in cmd.lower() for x in DANGEROUS)

@app.post("/terminal/run")
def run_terminal(data: dict):
    cmd = data.get("command", "")

    if is_dangerous(cmd):
        return {"error": "Blocked by Security Sentinel"}

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        return {
            "output": result.stdout,
            "error": result.stderr
        }
    except Exception as e:
        return {"error": str(e)}

# ------------------------
# TASKS (PLACEHOLDER)
# ------------------------
@app.get("/tasks")
def get_tasks():
    return [
        {"id": 1, "task": "System check", "status": "complete"},
        {"id": 2, "task": "Agent sync", "status": "running"}
    ]

# ------------------------
# SECURITY LOGS
# ------------------------
@app.get("/security/logs")
def security_logs():
    return [{"event": "No threats detected"}]

# ------------------------
# CHAT (MOBILE CHAT UI)
# ------------------------

@app.post("/chat")
def chat(data: dict):
    message = data.get("message", "")

    result = call_llm(message)

    return {
        "response": result,
        "status": "ok"
    }

# --- Health Check and Frontend Redirect ---
@app.get("/")
def root():
    return RedirectResponse(url="/static/ui-v2/index-merged.html")
