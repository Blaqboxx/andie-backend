import threading
import time
import subprocess
from andie_core.patch_engine import (
    safe_apply_patch, log_task, load_memory, recall_fix,
    create_task, plan_task, execute_task, verify_system, extract_error_context, has_actionable_error
)



# ------------------------
# CONFIG
# ------------------------
CHECK_INTERVAL = 10

# ------------------------
# SAFETY
# ------------------------
BLOCKED = ["rm -rf", "shutdown", "reboot"]

def is_safe(cmd: str) -> bool:
    return not any(b in cmd for b in BLOCKED)

# ------------------------
# OBSERVER
# ------------------------
def get_latest_error():
    try:
        return subprocess.getoutput("tail -n 50 logs.txt")
    except:
        return ""

# ------------------------
# LLM DECISION (HOOK)
# ------------------------
def diagnose_and_fix(error_log: str, file_path: str) -> str:
    # Replace with real LLM call
    print("🧠 Diagnosing error for:", file_path)
    # LLM must return UNIFIED DIFF FORMAT ONLY
    return f"""--- a/{file_path}\n+++ b/{file_path}\n@@\n- print('bad')\n+ print('good')\n"""

# ------------------------
# VALIDATION
# ------------------------
def validate_code() -> bool:
    result = subprocess.getoutput("python -m compileall .")
    return "Error" not in result and "Traceback" not in result

# ------------------------
# EXECUTION
# ------------------------



# ------------------------
# MAIN LOOP
# ------------------------
def andie_loop():
    print("🔥 ANDIE Autonomous Daemon Started")
