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
from pathlib import Path
from andie_core.validation_utils import validate_python, backup_file, rollback_file, get_llm_prompt

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

def diagnose_and_fix(error_log):
    # Placeholder for LLM call
    # Replace with your LLM integration
    print("[LLM] Diagnosing error:", error_log[-200:])
    # Example: always return a safe echo command
    return "echo 'No-op fix'"

def andie_task_loop():
    while True:
        # 1. Observer: Detect error
        error = get_latest_error()
        if "SyntaxError" in error or "Traceback" in error:
            print("⚠️ ANDIE detected issue")
            # 2. Reasoner: Propose fix
            # Harden LLM prompt
            prompt = get_llm_prompt(error)
            fix_cmd = diagnose_and_fix(prompt)
            print("🧠 ANDIE suggests:", fix_cmd)
            # 3. Executor: Confirm and apply
            if is_safe(fix_cmd):
                # Backup main files before edit (example: main.py)
                backup_file("main.py")
                queue.add_task({"cmd": fix_cmd, "error": error, "ts": time.time()})
                result = subprocess.getoutput(fix_cmd)
                print("🔧 FIX RESULT:", result)
                queue.complete_task(fix_cmd, result)
                # Validate before restart
                if validate_python():
                    print("✅ Code valid, restarting server")
                    subprocess.getoutput("pkill -f uvicorn")
                    subprocess.Popen("uvicorn main:app --reload", shell=True)
                else:
                    print("❌ ANDIE detected broken code — rolling back")
                    rollback_file("main.py")
        time.sleep(10)

threading.Thread(target=andie_task_loop, daemon=True).start()
