"""
bootstrap.py — ANDIE Trainstation main controller.
Replaces `docker compose up`. Ordered, validated, deterministic.

Usage:
    python3 /mnt/ai-ssd/valhalla/andie_backend/andie/trainstation/bootstrap.py
    python3 ... --preflight-only
    python3 ... --service backend
"""
from __future__ import annotations
import argparse
import sys
import time
import socket
import subprocess
from pathlib import Path

# Allow direct execution from any working dir
_HERE = Path(__file__).resolve().parent
_BACKEND_ROOT = _HERE.parents[2]  # andie_backend/
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

COMPOSE_DIR = "/mnt/ai-ssd/valhalla"

# Ordered startup sequence — each entry: (compose_service_name, health_url_or_None, timeout_s)
STARTUP_SEQUENCE = [
    ("backend",  "http://localhost:8010/health",  60),
    ("ui",       "http://localhost:5173/",         30),
]

# Optional governance layer (only started if --governance flag)
GOVERNANCE_SEQUENCE = [
    ("guardian", "http://localhost:7010/health", 45),
    ("mcp",      "http://localhost:7001/status",  45),
    ("sentinel", "http://localhost:7002/health",  45),
]


def _banner(text: str):
    width = 60
    print("\n" + "━" * width)
    print(f"  🚉 {text}")
    print("━" * width)


def _log(level: str, msg: str):
    icons = {"INFO": "·", "OK": "✓", "WARN": "⚠", "ERROR": "✗", "BOOT": "▶", "SKIP": "~"}
    icon = icons.get(level, "·")
    print(f"  {icon} [{level}] {msg}", flush=True)


def _http_ok(url: str) -> bool:
    import urllib.request
    try:
        r = urllib.request.urlopen(url, timeout=3)
        return r.status < 500
    except Exception:
        return False


def _tcp_ok(host: str, port: int) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=2)
        s.close()
        return True
    except Exception:
        return False


def run_preflight() -> bool:
    from andie_backend.andie.trainstation.preflight import run_preflight as _pf
    result = _pf()

    _banner("PREFLIGHT SCAN")
    for c in result["checks"]:
        icon = {"healthy": "✓", "degraded": "⚠", "failed": "✗", "unknown": "?"}.get(c["status"], "·")
        print(f"  {icon} {c['check']}: {c['detail']}")
        if "action" in c:
            print(f"    → {c['action']}")

    print()
    if result["failures"]:
        _log("ERROR", f"{len(result['failures'])} critical failures — cannot start stack:")
        for f in result["failures"]:
            print(f"    ✗ {f['check']}: {f['detail']}")
            if "action" in f:
                print(f"      → {f['action']}")
        return False

    if result["warnings"]:
        _log("WARN", f"{len(result['warnings'])} warnings (continuing):")
        for w in result["warnings"]:
            print(f"    ⚠ {w['check']}: {w['detail']}")

    _log("OK", "Preflight passed")
    return True


def start_service(name: str, health_url: str | None, timeout_s: int, build: bool = False) -> bool:
    _banner(f"STARTING: {name.upper()}")
    _log("BOOT", f"docker compose up -d {'--build ' if build else ''}{name}")

    cmd = ["docker", "compose", "up", "-d"]
    if build:
        cmd.append("--build")
    cmd.append(name)

    r = subprocess.run(cmd, cwd=COMPOSE_DIR, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        _log("ERROR", f"compose up failed:\n{r.stderr[:300]}")
        return False

    _log("OK", f"{name} container started")

    if not health_url:
        _log("SKIP", "no health URL — assuming healthy")
        return True

    _log("INFO", f"waiting for healthy — {health_url} (timeout: {timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        if _http_ok(health_url):
            latency = round((time.monotonic() - (deadline - timeout_s)) * 1000)
            _log("OK", f"{name} healthy after {attempt} polls (~{latency}ms)")
            return True
        remaining = round(deadline - time.monotonic())
        print(f"  · [{attempt}] not yet healthy — {remaining}s remaining", end="\r", flush=True)
        time.sleep(2)

    print()
    _log("ERROR", f"{name} did not become healthy within {timeout_s}s")
    _log("INFO", f"check logs: docker logs {name}")
    return False


def stop_all():
    _banner("STOPPING STACK")
    r = subprocess.run(["docker", "compose", "down"], cwd=COMPOSE_DIR,
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        _log("OK", "stack stopped")
    else:
        _log("WARN", f"compose down: {r.stderr[:200]}")


def build_ui():
    """Build the Vite UI inside the running container."""
    _banner("BUILDING UI")
    r = subprocess.run(
        ["docker", "exec", "andie-ui", "sh", "-c", "rm -rf /app/dist && npm run build"],
        capture_output=True, text=True, timeout=120
    )
    if r.returncode == 0:
        _log("OK", "UI built successfully")
        subprocess.run(["docker", "restart", "andie-ui"], capture_output=True, timeout=30)
        _log("OK", "andie-ui restarted with new build")
        return True
    _log("ERROR", f"UI build failed:\n{r.stderr[-300:]}")
    return False


def print_registry():
    """Print live registry snapshot."""
    _banner("RUNTIME REGISTRY")
    try:
        import urllib.request, json
        r = urllib.request.urlopen("http://localhost:8010/registry", timeout=5)
        data = json.loads(r.read())
        overall = data.get("overall", "unknown")
        print(f"  Overall: {overall.upper()}\n")
        for name, svc in data.get("services", {}).items():
            status = svc.get("status", "?")
            detail = svc.get("detail", "")
            latency = svc.get("latency_ms", "")
            latency_str = f" ({latency}ms)" if latency else ""
            icon = {"online": "✓", "degraded": "⚠", "offline": "✗", "unknown": "?"}.get(status, "·")
            print(f"  {icon} {name:<12} {status:<10} {detail}{latency_str}")
    except Exception as e:
        _log("WARN", f"registry not yet available: {e}")


def boot(governance: bool = False, build: bool = False) -> bool:
    _banner("ANDIE TRAINSTATION — BOOT SEQUENCE")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')} | Blaqtower2 | ANDIE Stack\n")

    if not run_preflight():
        _log("ERROR", "Preflight failed — aborting boot")
        return False

    sequence = STARTUP_SEQUENCE[:]
    if governance:
        sequence = sequence[:1] + GOVERNANCE_SEQUENCE + sequence[1:]

    for name, health_url, timeout_s in sequence:
        ok = start_service(name, health_url, timeout_s, build=build)
        if not ok:
            _log("ERROR", f"BOOT HALTED at: {name}")
            _log("INFO", "partial stack may be running — inspect with: docker ps")
            return False

    if build:
        build_ui()

    time.sleep(2)
    print_registry()

    _banner("STACK OPERATIONAL")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ANDIE Trainstation boot controller")
    parser.add_argument("--preflight-only", action="store_true", help="Run preflight checks and exit")
    parser.add_argument("--governance", action="store_true", help="Also start governance layer (guardian/mcp/sentinel)")
    parser.add_argument("--build", action="store_true", help="Rebuild containers before starting")
    parser.add_argument("--build-ui", action="store_true", help="Build Vite UI after stack starts")
    parser.add_argument("--registry", action="store_true", help="Print live registry and exit")
    parser.add_argument("--stop", action="store_true", help="Stop the full stack")
    args = parser.parse_args()

    if args.stop:
        stop_all()
        sys.exit(0)

    if args.registry:
        print_registry()
        sys.exit(0)

    if args.preflight_only:
        passed = run_preflight()
        sys.exit(0 if passed else 1)

    ok = boot(governance=args.governance, build=args.build)
    if ok and args.build_ui:
        build_ui()

    sys.exit(0 if ok else 1)
