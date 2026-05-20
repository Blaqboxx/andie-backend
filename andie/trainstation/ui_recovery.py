"""
ui_recovery.py — Frontend repair pipeline.

Ordered recovery actions, each more invasive than the last.
Each action returns {success, action, detail, next_action}.

Pipeline:
  1. restart_container      — soft restart, no rebuild
  2. rebuild_assets         — rm dist + npm run build + restart
  3. clear_node_modules     — full dependency reinstall + rebuild
  4. rollback_last_mutation — git stash last uncommitted change + rebuild

Call recover() to run the minimal required action,
or run_full_pipeline() to force all stages.
"""
from __future__ import annotations
import subprocess
import time
from pathlib import Path

COMPOSE_DIR   = "/app"
UI_DIR        = "/app/andie-ui"
CONTAINER_UI  = "andie-ui"
_LOG_PREFIX   = "[TRAINSTATION:UI]"


def _log(level: str, msg: str):
    icons = {"INFO": "·", "OK": "✓", "WARN": "⚠", "ERROR": "✗"}
    print(f"{_LOG_PREFIX} {icons.get(level,'·')} [{level}] {msg}", flush=True)


def _docker_exec(cmd: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["docker", "exec", CONTAINER_UI, "sh", "-c", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _host_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _wait_ui_up(timeout_s: int = 30) -> bool:
    """Poll localhost:5173 until it responds."""
    import urllib.request
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = urllib.request.urlopen("http://192.168.50.183:5173/", timeout=2)
            if r.status < 500:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# ── Recovery Actions ──────────────────────────────────────────────────────────

def restart_container() -> dict:
    """Action 1: Soft restart — no rebuild."""
    _log("INFO", "restart_container — docker restart andie-ui")
    rc, out = _host_cmd(["docker", "restart", CONTAINER_UI])
    if rc != 0:
        return {"success": False, "action": "restart_container", "detail": f"docker restart failed: {out[:120]}"}
    up = _wait_ui_up(30)
    if not up:
        return {"success": False, "action": "restart_container", "detail": "container restarted but UI not responding"}
    _log("OK", "restart succeeded — UI responding")
    return {"success": True, "action": "restart_container", "detail": "container restarted + UI up"}


def rebuild_assets() -> dict:
    """Action 2: Clear dist and rebuild JS/CSS bundle."""
    _log("INFO", "rebuild_assets — rm -rf dist && npm run build")
    rc, out = _docker_exec("rm -rf /app/dist && npm run build", timeout=180)
    if rc != 0:
        last_lines = "\n".join(out.splitlines()[-8:])
        return {"success": False, "action": "rebuild_assets", "detail": f"build failed:\n{last_lines}"}

    _log("OK", "build succeeded — restarting container")
    rc2, _ = _host_cmd(["docker", "restart", CONTAINER_UI])
    if rc2 != 0:
        return {"success": False, "action": "rebuild_assets", "detail": "build ok but restart failed"}
    up = _wait_ui_up(30)
    if not up:
        return {"success": False, "action": "rebuild_assets", "detail": "rebuilt but UI not responding post-restart"}

    _log("OK", "rebuild complete + UI up")
    return {"success": True, "action": "rebuild_assets", "detail": "dist rebuilt + container restarted"}


def clear_node_modules() -> dict:
    """Action 3: Nuclear — rm node_modules + npm ci + rebuild."""
    _log("WARN", "clear_node_modules — this will take ~2-5 minutes")
    rc, out = _docker_exec(
        "rm -rf /app/node_modules && npm ci && npm run build",
        timeout=600
    )
    if rc != 0:
        last_lines = "\n".join(out.splitlines()[-12:])
        return {"success": False, "action": "clear_node_modules", "detail": f"npm ci / build failed:\n{last_lines}"}

    rc2, _ = _host_cmd(["docker", "restart", CONTAINER_UI])
    up = _wait_ui_up(30)
    if not up:
        return {"success": False, "action": "clear_node_modules", "detail": "npm ci ok but UI not responding"}

    _log("OK", "node_modules reinstalled + rebuilt + UI up")
    return {"success": True, "action": "clear_node_modules", "detail": "full dependency reinstall complete"}


def rollback_last_mutation() -> dict:
    """Action 4: Rollback last uncommitted src mutation via git checkout."""
    _log("WARN", "rollback_last_mutation — git checkout HEAD -- src/")
    rc, out = _host_cmd(
        ["git", "checkout", "HEAD", "--", "src/"],
        cwd=UI_DIR
    )
    if rc != 0:
        return {"success": False, "action": "rollback_last_mutation", "detail": f"git checkout failed: {out[:120]}"}

    _log("OK", "rollback done — triggering rebuild")
    rebuild_result = rebuild_assets()
    if not rebuild_result["success"]:
        return {"success": False, "action": "rollback_last_mutation", "detail": f"rollback ok but rebuild failed: {rebuild_result['detail']}"}

    return {"success": True, "action": "rollback_last_mutation", "detail": "src rolled back + rebuilt successfully"}


# ── Validation After Recovery ─────────────────────────────────────────────────

def validate_after_recovery() -> dict:
    """Run ui_health checks and return visibility score."""
    from andie_backend.andie.trainstation.ui_health import run_checks
    result = run_checks()
    return {
        "visibility_score": result["visibility_score"],
        "overall":          result["overall"],
        "blank_screen":     result["blank_screen"],
        "mounted":          result["mounted"],
        "failures":         [c["check"] for c in result["failures"]],
    }


# ── Ordered Pipeline ─────────────────────────────────────────────────────────

PIPELINE = [
    ("restart_container",   restart_container),
    ("rebuild_assets",      rebuild_assets),
    ("clear_node_modules",  clear_node_modules),
    ("rollback_last_mutation", rollback_last_mutation),
]


def recover(max_actions: int = 2, target_score: int = 80) -> dict:
    """
    Run recovery actions in order until UI reaches target_score.
    Stops early on success. max_actions limits escalation depth.
    """
    _log("INFO", f"starting recovery pipeline — target score: {target_score}")
    history = []

    for name, action_fn in PIPELINE[:max_actions]:
        _log("INFO", f"trying: {name}")
        result = action_fn()
        history.append(result)

        if result["success"]:
            validation = validate_after_recovery()
            history[-1]["validation"] = validation
            if validation["visibility_score"] >= target_score:
                _log("OK", f"recovered! score={validation['visibility_score']} via {name}")
                return {
                    "recovered": True,
                    "action_taken": name,
                    "visibility_score": validation["visibility_score"],
                    "history": history,
                }
            _log("WARN", f"{name} succeeded but score {validation['visibility_score']} < {target_score} — escalating")
        else:
            _log("ERROR", f"{name} failed: {result['detail'][:100]}")

    # Exhausted pipeline
    return {
        "recovered":   False,
        "action_taken": None,
        "history":     history,
        "message":     "recovery pipeline exhausted — manual intervention required",
    }


def run_full_pipeline() -> dict:
    """Force all recovery stages regardless of success."""
    return recover(max_actions=len(PIPELINE), target_score=100)


if __name__ == "__main__":
    import sys, json
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if mode == "full":
        result = run_full_pipeline()
    elif mode == "rebuild":
        result = rebuild_assets()
    elif mode == "restart":
        result = restart_container()
    elif mode == "rollback":
        result = rollback_last_mutation()
    else:
        result = recover()
    print(json.dumps(result, indent=2))
