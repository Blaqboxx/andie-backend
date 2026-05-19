"""
recovery.py — Failure detection, retry, and repair hooks with structured logs.
"""
from __future__ import annotations
import subprocess
import time
from typing import Callable


class RecoveryFailure(Exception):
    pass


def _log(level: str, service: str, message: str, action: str = "", pid: int = None):
    """Structured trainstation log line."""
    parts = [f"[TRAINSTATION:{level}]", f"service={service}", message]
    if action:
        parts.append(f"action={action}")
    if pid:
        parts.append(f"pid={pid}")
    print(" | ".join(parts), flush=True)


def retry(
    fn: Callable,
    service: str,
    max_attempts: int = 3,
    delay_s: float = 5.0,
    backoff: float = 1.5,
) -> any:
    """
    Retry fn() up to max_attempts times with exponential backoff.
    Logs each attempt with structured output.
    Raises RecoveryFailure if all attempts exhausted.
    """
    last_err = None
    wait = delay_s
    for attempt in range(1, max_attempts + 1):
        try:
            result = fn()
            _log("RECOVERY", service, f"succeeded on attempt {attempt}/{max_attempts}")
            return result
        except Exception as e:
            last_err = e
            _log("WARN", service,
                 f"attempt {attempt}/{max_attempts} failed: {type(e).__name__}: {e}",
                 action=f"retrying in {round(wait)}s")
            if attempt < max_attempts:
                time.sleep(wait)
                wait *= backoff

    raise RecoveryFailure(
        f"[TRAINSTATION]\n"
        f"Service startup failed: {service}\n"
        f"- Attempts: {max_attempts}\n"
        f"- Last error: {last_err}\n"
        f"- Recommended action: check logs with: docker logs {service}"
    )


def wait_for_healthy(
    service: str,
    check_fn: Callable[[], bool],
    timeout_s: float = 60.0,
    poll_s: float = 2.0,
    label: str = "",
) -> bool:
    """
    Poll check_fn() until it returns True or timeout.
    Returns True if healthy, False if timed out.
    """
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            if check_fn():
                _log("OK", service, f"healthy after {attempt} polls{' — ' + label if label else ''}")
                return True
        except Exception as e:
            pass
        remaining = round(deadline - time.monotonic())
        _log("WAIT", service, f"not yet healthy (attempt {attempt}, {remaining}s remaining)")
        time.sleep(poll_s)

    _log("TIMEOUT", service,
         f"did not become healthy within {timeout_s}s",
         action=f"check logs: docker logs {service}")
    return False


def diagnose_port_conflict(port: int, service: str) -> dict:
    """Identify what's holding a port and suggest action."""
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-n", "-P"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) > 1:
            occupant = lines[1].split()
            pid = occupant[1] if len(occupant) > 1 else "unknown"
            cmd = occupant[0] if occupant else "unknown"
            return {
                "port": port,
                "service": service,
                "occupied_by": cmd,
                "pid": pid,
                "action": f"kill -9 {pid}  # or stop the conflicting service",
            }
    except Exception:
        pass
    return {
        "port": port,
        "service": service,
        "occupied_by": "unknown",
        "action": f"lsof -i :{port} to investigate",
    }


def docker_compose_up(service: str, compose_dir: str, build: bool = False) -> bool:
    """Start a single docker compose service. Returns True on success."""
    cmd = ["docker", "compose", "up", "-d"]
    if build:
        cmd.append("--build")
    cmd.append(service)
    try:
        r = subprocess.run(cmd, cwd=compose_dir, capture_output=True, text=True, timeout=180)
        if r.returncode == 0:
            _log("OK", service, "docker compose up succeeded")
            return True
        _log("ERROR", service, f"docker compose up failed: {r.stderr[:200]}",
             action="check Dockerfile and compose config")
        return False
    except subprocess.TimeoutExpired:
        _log("TIMEOUT", service, "docker compose up timed out after 180s")
        return False
    except Exception as e:
        _log("ERROR", service, str(e))
        return False
