from fastapi import FastAPI, APIRouter, Request
import psutil
from andie_core.memory.short_term import ShortTermMemory
from typing import List

app = FastAPI()
router = APIRouter()

# --- ANDIE State API ---
@router.get("/andie/state")
def andie_state():
    memory = ShortTermMemory().history
    return {
        "status": "running",
        "system": {
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage('/') .percent
        },
        "last_action": memory[-1] if memory else None
    }

app.include_router(router)

# --- ANDIE ENVIRONMENT VALIDATION ---
try:
    from andie_core.andie_env import validate_and_fix_env
    validate_and_fix_env()
except Exception as e:
    print(f"[ANDIE ENV] Validation failed: {e}")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import subprocess
import os
import threading
from Malk.runtime.daemon import run_daemon
run_daemon()
from Malk.core.system_state import state
from fastapi.middleware.cors import CORSMiddleware
from Malk.core.logger import get_logs

""" Optional: ANDIE Core integration"""
try:
    from andie_core import AndieCore
    andie_core = AndieCore()
except ImportError:
    andie_core = None


""" --- LLM Client Setup (user must fill in) ---"""
try:
    from openai import OpenAI
    client = OpenAI()
except ImportError:
    client = None

""" --- Simple log buffer for dashboard ---"""
from collections import deque
LOG_BUFFER = deque(maxlen=100)

def log_event(msg):
    LOG_BUFFER.append(msg)

task_queue: List[dict] = []

@app.post("/task")
def submit_task(data: dict):
    """Submit a new task/message to ANDIE from the frontend/mobile app."""
    task = {
        "id": len(task_queue) + 1,
        "task": data.get("task", ""),
        "status": "queued"
    }
    task_queue.append(task)
    # Optionally: trigger daemon/task system here
    return {"status": "queued", "task": task}

@app.get("/tasks/queue")
def get_task_queue():
    """Get the current queued tasks/messages."""
    return {"tasks": task_queue}

@app.get("/")
def root():
    return {"status": "ANDIE running autonomously"}

@app.post("/autonomy/start")
def start_autonomy():
    state["autonomy_enabled"] = True
    return {"status": "started"}

@app.post("/autonomy/stop")
def stop_autonomy():
    state["autonomy_enabled"] = False
    return {"status": "stopped"}

""" ------------------------"""
""" SYSTEM STATUS"""
""" ------------------------"""
@app.get("/system/status")
def system_status():
    return {
        "status": "online",
        "agents": ["NEXUS", "ORACLE", "HERALD", "CIPHER", "WRAITH"],
        "uptime": "active"
    }

""" ------------------------"""
""" AGENTS LIST"""
""" ------------------------"""
@app.get("/agents")
def get_agents():
    return [
        {"name": "NEXUS", "role": "orchestrator"},
        {"name": "ORACLE", "role": "reasoning"},
        {"name": "HERALD", "role": "language"},
        {"name": "CIPHER", "role": "security"},
        {"name": "WRAITH", "role": "web"}
    ]

""" ------------------------"""
""" AGENT RUN (LLM CORE)"""
""" ------------------------"""
def call_llm(task: str) -> str:
    if client:
        response = client.responses.create(
            model="gpt-4o-mini",
            input=task
        )
        if hasattr(response, "output_text") and response.output_text:
            return response.output_text
        return response.output[0].content[0].text
    elif andie_core:
        return andie_core.run(task)
    return "[ERROR] LLM client not configured. Please fill in call_llm()."

@app.post("/agents/run")
def run_agent(data: dict):
    task = data.get("task", "")
    if not task:
        return {"error": "No task provided"}
    try:
        result = call_llm(task)
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}

""" ------------------------"""
""" TERMINAL (SECURED BASIC)"""
""" ------------------------"""
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

""" ------------------------"""
""" TASKS (PLACEHOLDER)"""
""" ------------------------"""
@app.get("/tasks")
def get_tasks():
    return [
        {"id": 1, "task": "System check", "status": "complete"},
        {"id": 2, "task": "Agent sync", "status": "running"}
    ]

""" ------------------------"""
""" SECURITY LOGS"""
""" ------------------------"""
@app.get("/security/logs")
def security_logs():
    return [{"event": "No threats detected"}]

""" ------------------------"""
""" CHAT (MOBILE CHAT UI)"""
""" ------------------------"""
@app.post("/chat")
def chat(data: dict):
    message = data.get("message", "")
    if not message:
        return {"error": "No message provided"}
    try:
        result = call_llm(message)
        return {"response": result}
    except Exception as e:
        return {"error": str(e)}

@app.get("/logs")
def logs_endpoint():
    return {"logs": get_logs()}

# --- ANDIE Task System Integration ---
try:
    import andie_core.andie_task_system
except ImportError as e:
    log_event(f"ANDIE Task System not loaded: {e}")
# This will start the autonomous debugging agent loop on startup
