"""Probe: storage / disk health."""
from __future__ import annotations
import os
from pathlib import Path

PATHS = [
    {"name": "app-workspace",  "path": "/app/workspace",          "warn_pct": 85, "crit_pct": 95},
    {"name": "andie-memory",   "path": "/app/andie_memory",        "warn_pct": 85, "crit_pct": 95},
    {"name": "root-fs",        "path": "/",                        "warn_pct": 85, "crit_pct": 95},
]


def _check_path(name: str, path: str, warn_pct: int, crit_pct: int) -> dict:
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bfree * st.f_frsize
        used_pct = round((1 - free / total) * 100, 1) if total else 0

        total_gb = round(total / 1e9, 1)
        free_gb = round(free / 1e9, 1)
        detail = f"{used_pct}% used — {free_gb}GB free of {total_gb}GB"

        if used_pct >= crit_pct:
            status = "failed"
        elif used_pct >= warn_pct:
            status = "degraded"
        else:
            status = "healthy"

        return {"check": name, "status": status, "detail": detail, "used_pct": used_pct}
    except Exception as e:
        return {"check": name, "status": "unknown", "detail": str(e)[:120]}


async def run() -> list[dict]:
    checks = []
    for p in PATHS:
        checks.append(_check_path(**p))

    # Artifact count
    artifacts_dir = Path("/app/workspace/artifacts")
    if artifacts_dir.exists():
        count = sum(1 for d in artifacts_dir.iterdir() if d.is_dir())
        checks.append({"check": "artifact-builds", "status": "healthy", "detail": f"{count} builds on disk"})

    return checks
