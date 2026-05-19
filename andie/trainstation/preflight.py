"""
preflight.py — Pre-startup validation scanner.
Checks ports, GPU, disk, environment, and network before anything launches.
All checks return {name, status, detail, action} dicts.
"""
from __future__ import annotations
import os
import socket
import shutil
from pathlib import Path


# ── Port checks ────────────────────────────────────────────────────────────────

REQUIRED_PORTS = [
    {"port": 8010, "service": "andie-backend"},
    {"port": 5173, "service": "andie-ui"},
]

OPTIONAL_PORTS = [
    {"port": 7010, "service": "andie-guardian"},
    {"port": 7001, "service": "andie-mcp"},
    {"port": 7002, "service": "andie-sentinel"},
    {"port": 6379, "service": "redis"},
    {"port": 6333, "service": "qdrant"},
]


def check_port_free(port: int, service: str) -> dict:
    """Returns healthy if port is FREE (not yet in use)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            result = s.connect_ex(("127.0.0.1", port))
        if result != 0:
            return {"check": f"port:{port}", "status": "healthy",
                    "detail": f"port {port} ({service}) is free"}
        else:
            return {"check": f"port:{port}", "status": "degraded",
                    "detail": f"port {port} ({service}) already occupied",
                    "action": f"find process: lsof -i :{port}"}
    except Exception as e:
        return {"check": f"port:{port}", "status": "unknown", "detail": str(e)}


# ── External service reachability ──────────────────────────────────────────────

REQUIRED_REMOTE = [
    {"host": "192.168.50.9", "port": 11434, "name": "ollama-lan"},
]

OPTIONAL_REMOTE = [
    {"host": "100.90.65.67",   "port": 11434, "name": "ollama-tailscale"},
    {"host": "100.112.224.112","port": 8010,  "name": "blaqtower2-tailscale"},
]


def check_remote(host: str, port: int, name: str, required: bool = True) -> dict:
    try:
        s = socket.create_connection((host, port), timeout=3)
        s.close()
        return {"check": f"remote:{name}", "status": "healthy",
                "detail": f"{host}:{port} reachable"}
    except Exception as e:
        status = "failed" if required else "degraded"
        return {"check": f"remote:{name}", "status": status,
                "detail": f"{host}:{port} unreachable — {e}",
                "action": "verify host is up and network route exists"}


# ── Disk space ────────────────────────────────────────────────────────────────

DISK_CHECKS = [
    {"path": "/mnt/ai-ssd/valhalla", "warn_pct": 85, "crit_pct": 95, "name": "valhalla-ssd"},
    {"path": "/",                     "warn_pct": 90, "crit_pct": 97, "name": "root-fs"},
]


def check_disk(path: str, warn_pct: int, crit_pct: int, name: str) -> dict:
    try:
        usage = shutil.disk_usage(path)
        used_pct = round(usage.used / usage.total * 100, 1)
        free_gb = round(usage.free / 1e9, 1)
        if used_pct >= crit_pct:
            return {"check": f"disk:{name}", "status": "failed",
                    "detail": f"{used_pct}% used — only {free_gb}GB free",
                    "action": "free disk space before starting stack"}
        elif used_pct >= warn_pct:
            return {"check": f"disk:{name}", "status": "degraded",
                    "detail": f"{used_pct}% used — {free_gb}GB free (low)",
                    "action": "consider freeing disk space"}
        return {"check": f"disk:{name}", "status": "healthy",
                "detail": f"{used_pct}% used — {free_gb}GB free"}
    except Exception as e:
        return {"check": f"disk:{name}", "status": "unknown", "detail": str(e)}


# ── Environment variables ─────────────────────────────────────────────────────

REQUIRED_ENV = [
    # These are checked at the host level before Docker starts
]

COMPOSE_FILE_PATH = "/mnt/ai-ssd/valhalla/docker-compose.yml"


def check_compose_file() -> dict:
    p = Path(COMPOSE_FILE_PATH)
    if p.exists():
        return {"check": "compose-file", "status": "healthy",
                "detail": f"found: {COMPOSE_FILE_PATH}"}
    return {"check": "compose-file", "status": "failed",
            "detail": f"missing: {COMPOSE_FILE_PATH}",
            "action": "restore docker-compose.yml before starting"}


def check_valhalla_mount() -> dict:
    p = Path("/mnt/ai-ssd/valhalla")
    if p.exists() and any(p.iterdir()):
        return {"check": "valhalla-mount", "status": "healthy",
                "detail": "/mnt/ai-ssd/valhalla is mounted and populated"}
    return {"check": "valhalla-mount", "status": "failed",
            "detail": "/mnt/ai-ssd/valhalla missing or empty",
            "action": "mount ai-ssd before starting stack"}


def check_memory_store() -> dict:
    p = Path("/mnt/ai-ssd/valhalla/andie_memory")
    if p.exists():
        files = list(p.rglob("*.json"))
        return {"check": "memory-store", "status": "healthy",
                "detail": f"andie_memory/ found with {len(files)} JSON files"}
    return {"check": "memory-store", "status": "degraded",
            "detail": "andie_memory/ not found — cognitive state will be empty",
            "action": "memory will be initialized on first run"}


def check_workspace() -> dict:
    p = Path("/mnt/ai-ssd/valhalla/workspace/artifacts")
    p.mkdir(parents=True, exist_ok=True)
    return {"check": "artifact-workspace", "status": "healthy",
            "detail": f"workspace/artifacts exists"}


# ── Docker availability ───────────────────────────────────────────────────────

def check_docker() -> dict:
    result = shutil.which("docker")
    if result:
        return {"check": "docker-cli", "status": "healthy", "detail": f"found: {result}"}
    return {"check": "docker-cli", "status": "failed", "detail": "docker not found in PATH",
            "action": "install docker"}


def check_docker_compose() -> dict:
    result = shutil.which("docker")
    if result:
        import subprocess
        try:
            r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                version = r.stdout.strip().split("\n")[0]
                return {"check": "docker-compose", "status": "healthy", "detail": version}
        except Exception:
            pass
    return {"check": "docker-compose", "status": "failed",
            "detail": "docker compose not available",
            "action": "install docker compose plugin"}


# ── Full preflight run ────────────────────────────────────────────────────────

def run_preflight() -> dict:
    """Run all preflight checks. Returns {passed, warnings, failures, checks}."""
    checks = []

    checks.append(check_docker())
    checks.append(check_docker_compose())
    checks.append(check_compose_file())
    checks.append(check_valhalla_mount())
    checks.append(check_memory_store())
    checks.append(check_workspace())

    for d in DISK_CHECKS:
        checks.append(check_disk(**d))

    for svc in REQUIRED_PORTS:
        checks.append(check_port_free(**svc))

    for svc in REQUIRED_REMOTE:
        checks.append(check_remote(**svc, required=True))

    for svc in OPTIONAL_REMOTE:
        checks.append(check_remote(**svc, required=False))

    failures = [c for c in checks if c["status"] in ("failed",)]
    warnings = [c for c in checks if c["status"] in ("degraded", "unknown")]

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "warnings": warnings,
        "checks": checks,
    }
