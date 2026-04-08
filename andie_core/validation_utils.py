import subprocess
import shutil
from pathlib import Path

BACKUP_SUFFIX = ".bak"

# --- Validation Layer ---
def validate_python():
    result = subprocess.getoutput("python -m compileall .")
    return "Error" not in result and "Traceback" not in result

# --- Backup System ---
def backup_file(path):
    p = Path(path)
    if p.exists():
        shutil.copy(str(p), str(p) + BACKUP_SUFFIX)

# --- Rollback System ---
def rollback_file(path):
    p = Path(path)
    bak = str(p) + BACKUP_SUFFIX
    if Path(bak).exists():
        shutil.copy(bak, str(p))

# --- LLM Prompt Hardening ---
def get_llm_prompt(error_log):
    return f"""
You are ANDIE, a system engineer.

STRICT RULES:
- NEVER use triple quotes (three double quotes in a row)
- Use # for comments ONLY
- All functions must have valid bodies (use pass for placeholders)
- Always maintain indentation

Error:
{error_log}

Return ONLY a safe fix command.
"""
