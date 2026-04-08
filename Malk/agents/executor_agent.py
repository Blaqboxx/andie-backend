import subprocess
import signal

""" Sentinel: Block dangerous patterns"""
DANGEROUS = ["rm -rf", "shutdown", "reboot", "mkfs", "dd if=", "os.remove", "os.rmdir", "os.system('reboot')"]

""" Forbidden imports"""
FORBIDDEN_IMPORTS = ["timeout_decorator", "requests", "os.system('sudo", "pip install"]

def is_safe(code: str) -> bool:
    if any(bad in code for bad in FORBIDDEN_IMPORTS):
        return False
    return not any(x in code for x in DANGEROUS)

def execute_task(code: str, timeout: int = 10):
    if not is_safe(code):
        return {"error": "Blocked by Sentinel: Dangerous or forbidden code detected."}
    try:
        # Run code in a subprocess with timeout
        proc = subprocess.Popen(
            ["python3", "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=lambda: signal.signal(signal.SIGALRM, signal.SIG_DFL)
        )
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return {"error": "Execution timed out."}
        return {
            "output": out.decode() if out else "",
            "error": err.decode() if err else ""
        }
    except Exception as e:
        return {"error": str(e)}
