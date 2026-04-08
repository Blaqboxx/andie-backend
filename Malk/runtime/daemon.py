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

    while True:
        print("🧠 ANDIE scanning system...")

        log_text = get_latest_error()
        context = extract_error_context(log_text)


        if not has_actionable_error(context):
            print("🟡 No actionable error detected")
            time.sleep(CHECK_INTERVAL)
            continue

        if not context["file"]:
            print("⚠️ No file detected — using main.py fallback")
            context["file"] = "main.py"

        def diagnose_and_fix_with_file(step):
            return diagnose_and_fix(step, context["file"])

        def safe_apply_patch_with_file(patch, _, validate_code):
            return safe_apply_patch(patch, context["file"], validate_code)

        task = create_task(context["error"])
        task["plan"] = plan_task(task)

        success = execute_task(
            task,
            diagnose_and_fix_with_file,
            safe_apply_patch_with_file,
            validate_code
        )

        if not success:
            print("🚨 Task failed")

        if verify_system(get_latest_error):
            print("✅ System stable")
        else:
            print("❌ System still broken")

        time.sleep(CHECK_INTERVAL)

# ------------------------
# START DAEMON
# ------------------------
def run_daemon():
    thread = threading.Thread(target=andie_loop, daemon=True)
    thread.start()
