"""Compatibility runtime for legacy worker.worker_runtime imports."""

from __future__ import annotations

from andie.core.agents.frontend_ui_agent import run_agent as _run_frontend


def execute_worker_payload(task: dict) -> dict:
    task_type = str((task or {}).get("type") or "")
    payload = (task or {}).get("payload") if isinstance((task or {}).get("payload"), dict) else {}

    if task_type == "frontend_issue":
        result = _run_frontend(
            {
                "prompt": str(payload.get("issue") or ""),
                "context": str(payload.get("context") or ""),
                "metadata": {"files": payload.get("files") if isinstance(payload.get("files"), list) else []},
            }
        )
        return {"status": "ok", "taskType": task_type, "result": result}

    return {"status": "error", "taskType": task_type, "error": "unsupported_task_type"}
