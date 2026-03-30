import subprocess
import uuid
import time

from valhalla.policy.validator import validate_code
from valhalla.sentinel.monitor import monitor_execution


def run_in_sandbox(code: str):
    # 🧠 STEP 1 — VALIDATE
    validation = validate_code(code)

    if validation["status"] != "SAFE":
        return {
            "status": "BLOCKED",
            "details": validation
        }

    # 🛡️ STEP 2 — START MONITORING
    start_time = time.time()

    container_name = f"valhalla_{uuid.uuid4()}"

    process = subprocess.Popen(
        [
            "docker", "run",
            "--rm",
            "--name", container_name,
            "--network", "none",
            "--memory", "128m",
            "--cpus", "0.5",
            "-i",
            "valhalla-python"
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        stdout, stderr = process.communicate(code, timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return {
            "status": "KILLED",
            "reason": "Execution timeout"
        }

    # 🛡️ STEP 3 — SENTINEL CHECK
    monitor_result = monitor_execution(start_time)

    if monitor_result["status"] != "OK":
        return monitor_result

    return {
        "status": "SUCCESS",
        "output": stdout.strip(),
        "error": stderr.strip(),
        "metrics": monitor_result
    }
