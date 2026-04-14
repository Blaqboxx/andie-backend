from __future__ import annotations

import os
from typing import Any, Dict


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def load_node_registry() -> Dict[str, Dict[str, Any]]:
    thinkpad_host = (os.environ.get("ANDIE_BRAIN_HOST") or os.environ.get("ANDIE_THINKPAD_HOST") or "127.0.0.1").strip()
    thinkpad_port = _int_env("ANDIE_BRAIN_PORT", _int_env("ANDIE_THINKPAD_BACKEND_PORT", 8000))

    nuc_host = (os.environ.get("ANDIE_NUC_WORKER_API_HOST") or os.environ.get("ANDIE_NUC_HOST") or "").strip()
    nuc_port = _int_env("ANDIE_NUC_WORKER_API_PORT", 9000)

    return {
        "thinkpad": {
            "id": "thinkpad",
            "role": "brain",
            "host": thinkpad_host,
            "port": thinkpad_port,
            "healthUrl": f"http://{thinkpad_host}:{thinkpad_port}/health",
            "executeUrl": f"http://{thinkpad_host}:{thinkpad_port}/orchestrator/run",
            "status": "unknown",
        },
        "nuc": {
            "id": "nuc",
            "role": "worker",
            "host": nuc_host,
            "port": nuc_port,
            "healthUrl": f"http://{nuc_host}:{nuc_port}/health" if nuc_host else None,
            "executeUrl": f"http://{nuc_host}:{nuc_port}/execute" if nuc_host else None,
            "status": "unknown" if nuc_host else "offline",
        },
    }