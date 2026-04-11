import os
import subprocess
import time

WORKSPACE_PATH = os.getenv("WORKSPACE_PATH", "/mnt/andie_storage")

def run_cmd(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

def detect_issues():
    issues = []
    required_paths = [
        f"{WORKSPACE_PATH}/agents",
        f"{WORKSPACE_PATH}/memory",
        f"{WORKSPACE_PATH}/vector_db",
        f"{WORKSPACE_PATH}/logs",
    ]
    for path in required_paths:
        if not os.path.exists(path):
            issues.append(("missing_path", path))
    return issues

def fix_issue(issue):
    issue_type, value = issue
    if issue_type == "missing_path":
        print(f"[SELF-HEAL] Creating: {value}")
        os.makedirs(value, exist_ok=True)

def run():
    print("[SELF-SERVICE] Agent active")
    while True:
        issues = detect_issues()
        if issues:
            print(f"[SELF-SERVICE] Issues: {issues}")
            for issue in issues:
                fix_issue(issue)
        else:
            print("[SELF-SERVICE] System healthy")
        time.sleep(30)
