import difflib
import os
import subprocess
import tempfile
import shutil
import json
import re

MEMORY_FILE = "memory.json"

class PatchEngine:
    """
    PatchEngine safely applies unified diff patches to files.
    It validates, applies, and can roll back changes if needed.
    """
    def __init__(self, backup_dir=".andie_patches"):
        self.backup_dir = backup_dir
        os.makedirs(backup_dir, exist_ok=True)

    def backup(self, file_path):
        base = os.path.basename(file_path)
        backup_path = os.path.join(self.backup_dir, base)
        with open(file_path, "r") as fsrc, open(backup_path, "w") as fdst:
            fdst.write(fsrc.read())
        return backup_path

    def restore(self, file_path):
        base = os.path.basename(file_path)
        backup_path = os.path.join(self.backup_dir, base)
        if os.path.exists(backup_path):
            with open(backup_path, "r") as fsrc, open(file_path, "w") as fdst:
                fdst.write(fsrc.read())
            return True
        return False

    def apply_patch(self, file_path, patch_text):
        """
        Apply a unified diff patch to file_path.
        Returns True if successful, False otherwise.
        """
        self.backup(file_path)
        with open(file_path, "r") as f:
            original = f.readlines()
        patched = list(difflib.restore(difflib.ndiff(original, patch_text.splitlines(keepends=True)), 2))
        with open(file_path, "w") as f:
            f.writelines(patched)
        return True

    def validate_patch(self, file_path, patch_text):
        """
        Validate that the patch applies cleanly and the file compiles (if Python).
        """
        try:
            self.apply_patch(file_path, patch_text)
            if file_path.endswith(".py"):
                import py_compile
                py_compile.compile(file_path, doraise=True)
            return True
        except Exception as e:
            self.restore(file_path)
            return False

# Example usage:
# patcher = PatchEngine()
# patcher.apply_patch("target.py", patch_text)

# Task log (in-memory)
patch_tasks = []
tasks = []

def load_memory():
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

def apply_patch(patch_text: str, target_file: str):
    try:
        with tempfile.NamedTemporaryFile(delete=False, mode="w") as f:
            f.write(patch_text)
            patch_path = f.name

        # backup
        shutil.copy(target_file, target_file + ".bak")

        result = subprocess.getoutput(f"patch {target_file} {patch_path}")
        print("PATCH RESULT:", result)

        return True

    except Exception as e:
        print("PATCH ERROR:", e)
        return False

def rollback(path):
    shutil.copy(path + ".bak", path)
    print("♻️ Rolled back:", path)

def safe_apply_patch(patch, file, validate_code):
    success = apply_patch(patch, file)
    if not success:
        return False
    if validate_code():
        return True
    print("❌ Patch broke code — rolling back")
    rollback(file)
    return False

def log_task(error, patch, success):
    patch_tasks.append({
        "error": error,
        "patch": patch,
        "success": success
    })
    # Persistent memory
    memory = load_memory()
    memory.append({
        "error": error,
        "patch": patch,
        "success": success
    })
    save_memory(memory)

def recall_fix(error, memory):
    for m in memory:
        if error in m["error"] and m["success"]:
            return m["patch"]
    return None

# --- Task Planning System ---
def create_task(error):
    return {
        "id": len(tasks) + 1,
        "error": error,
        "status": "pending",
        "retries": 0,
        "plan": None,
        "result": None
    }

def plan_task(task):
    # LLM stub: returns a simple plan as a list of steps
    # Replace with real LLM call for advanced planning
    return [
        "identify file with error",
        "fix syntax",
        "validate code",
        "restart server"
    ]

def execute_task(task, diagnose_and_fix, safe_apply_patch, validate_code):
    plan = task["plan"]
    for step in plan:
        print("🔧 Executing:", step)
        patch = diagnose_and_fix(step)
        success = safe_apply_patch(patch, "target_file.py", validate_code)
        if not success:
            task["retries"] += 1
            task["status"] = "failed"
            return False
    task["status"] = "completed"
    return True

def verify_system(get_latest_error):
    logs = get_latest_error()
    return "Traceback" not in logs and "Error" not in logs

def extract_error_file(error_log):
    """Extract the first Python file path from a traceback/error log."""
    matches = re.findall(r"File '([^']+\.py)'", error_log)
    if matches:
        return matches[0]
    return None

def extract_error_context(log_text: str):
    import re
    lines = log_text.splitlines()
    error_text = ""
    file_path = None

    # --- Pattern 1: Traceback ---
    if "Traceback" in log_text:
        capture = False
        block = []
        for line in lines:
            if "Traceback" in line:
                capture = True
                block = [line]
            elif capture:
                block.append(line)
                if line.strip() == "":
                    break
        error_text = "\n".join(block)
        match = re.search(r'File "(.+?)"', error_text)
        if match:
            file_path = match.group(1)

    # --- Pattern 2: SyntaxError ---
    elif "SyntaxError" in log_text:
        error_text = log_text
        match = re.search(r'File "(.+?)"', log_text)
        if match:
            file_path = match.group(1)

    # --- Pattern 3: ModuleNotFoundError ---
    elif "ModuleNotFoundError" in log_text:
        error_text = log_text

    # --- Pattern 4: Generic Error ---
    elif "Error" in log_text:
        error_text = log_text

    return {
        "error": error_text.strip(),
        "file": file_path
    }

def has_actionable_error(context):
    return bool(context["error"]) and len(context["error"]) > 20
