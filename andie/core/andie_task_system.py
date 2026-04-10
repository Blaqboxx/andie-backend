"""
ANDIE Task System: Autonomous Debugging Agent
- Task queue
- Persistent memory
- Retry logic
- Success tracking
"""


import threading
import time
import json
import subprocess
from andie.core.validation_utils import validate_python, backup_file, rollback_file, get_llm_prompt
from pathlib import Path

MEMORY_PATH = Path("andie_memory.json")

class TaskQueue:
    def __init__(self):
        self.load()

    def load(self):
        if MEMORY_PATH.exists():
            with open(MEMORY_PATH) as f:
                data = json.load(f)
                self.tasks = data.get("tasks", [])
                self.history = data.get("history", [])
        else:
            self.tasks = []
            self.history = []

    def save(self):
        with open(MEMORY_PATH, "w") as f:
            json.dump({"tasks": self.tasks, "history": self.history}, f, indent=2)

    def add_task(self, task):
        self.tasks.append(task)
        self.save()

    def next_task(self):
        if self.tasks:
            return self.tasks.pop(0)
        return None

    def complete_task(self, task, result):
        self.history.append({"task": task, "result": result, "ts": time.time()})
        self.save()

queue = TaskQueue()

SAFE_COMMANDS = ["nano", "sed", "pip install", "systemctl restart", "uvicorn", "kill", "rm", "touch"]

def is_safe(cmd):
    return any(cmd.startswith(c) for c in SAFE_COMMANDS)

def get_latest_error():
    # You can swap this for journalctl or log file tailing
    logs = subprocess.getoutput("tail -n 40 uvicorn.log")
    return logs
