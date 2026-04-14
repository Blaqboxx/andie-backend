from __future__ import annotations

import asyncio
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

import requests

from interfaces.api.dispatcher import classify_task, dispatch_task
from interfaces.api.security_sentinel import audit_security_event, signed_headers
from interfaces.api.workflow_engine import workflow_engine
from scheduler.queue import cancel_task, queue_metrics, recent_tasks, request_manual_retry


SYSTEM_PROMPT = (
    "You are ANDIE, the operator control surface for a distributed AI system. "
    "Respond concisely, operationally, and with concrete next actions."
)


def execution_enabled() -> bool:
    raw = os.environ.get("ANDIE_EXECUTION_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def command_snapshot(limit: int = 5) -> Dict[str, Any]:
    return {
        "updatedAt": utc_now(),
        "queue": queue_metrics(),
        "tasks": recent_tasks(limit),
    }


def extract_task_id(task_text: str) -> str | None:
    matches = re.findall(r"\b\d+\b", task_text or "")
    if not matches:
        return None
    return max(matches, key=len)


def is_heavy_task(task_text: str, params: Dict[str, Any]) -> bool:
    return classify_task(task_text, params) == "compute_heavy"


def format_queue_summary(snapshot: Dict[str, Any]) -> str:
    queue = snapshot.get("queue") or {}
    tasks = snapshot.get("tasks") or []
    pending = queue.get("pending", 0)
    running = queue.get("running", 0)
    done = queue.get("done", 0)
    failed = queue.get("failed", 0)
    cancelled = queue.get("cancelled", 0)
    recent = ", ".join(
        f"#{task.get('id')} {task.get('status', 'unknown')}"
        for task in tasks[:3]
    ) or "no recent tasks"
    return (
        f"System ready. Queue: {pending} pending, {running} running, {done} done, "
        f"{failed} failed, {cancelled} cancelled. Recent: {recent}."
    )


def invoke_llm(task_text: str, context: str, snapshot: Dict[str, Any]) -> str | None:
    if not os.environ.get("OPENAI_API_KEY"):
        return None

    try:
        from brain.llm_engine import think

        return think(
            {
                "prompt": task_text,
                "system": SYSTEM_PROMPT,
                "context": (
                    f"Operator context:\n{context}\n\n"
                    f"Runtime snapshot:\n{format_queue_summary(snapshot)}"
                ).strip(),
                "model": os.environ.get("ANDIE_CHAT_MODEL", "gpt-4o"),
            }
        )
    except Exception:
        return None


def restart_backend_process(shell_command: str) -> None:
    subprocess.Popen(shell_command, shell=True)


def execute_local_command(
    task_text: str,
    context: str,
    params: Dict[str, Any],
    restart_shell_command: str,
) -> Dict[str, Any]:
    snapshot = command_snapshot()
    lowered = (task_text or "").lower().strip()
    task_id = extract_task_id(task_text)

    if "restart backend" in lowered:
        if not execution_enabled():
            audit_security_event(
                "execution_blocked",
                {
                    "action": "restart_backend",
                    "reason": "execution_disabled",
                },
            )
            return {
                "status": "blocked",
                "route": "thinkpad",
                "targetNode": "thinkpad",
                "response": "Execution is disabled. Backend restart was blocked by safety policy.",
                "result": {"action": "restart_backend", "blocked": True, "reason": "execution_disabled"},
                "executedAt": utc_now(),
            }

        restart_backend_process(restart_shell_command)
        audit_security_event(
            "execution_action",
            {
                "action": "restart_backend",
                "mode": "local",
            },
        )
        return {
            "status": "completed",
            "route": "thinkpad",
            "targetNode": "thinkpad",
            "response": "Backend restart triggered. Expect a brief reconnect window while the API reloads.",
            "result": {"action": "restart_backend"},
            "executedAt": utc_now(),
        }

    if task_id and any(keyword in lowered for keyword in ("retry", "rerun", "re-run")):
        retried = request_manual_retry(task_id, preferred_node=params.get("preferredNode"))
        if retried is None:
            return {
                "status": "completed",
                "route": "thinkpad",
                "response": f"Retry could not be scheduled for task #{task_id}. It may be missing or not eligible.",
                "result": {"action": "retry", "taskId": task_id},
                "executedAt": utc_now(),
            }
        target_node = retried.get("preferredNode") or retried.get("assignedNode") or "the next available node"
        return {
            "status": "completed",
            "route": "thinkpad",
            "targetNode": target_node,
            "response": f"Retry scheduled for task #{task_id} on {target_node}.",
            "result": retried,
            "executedAt": utc_now(),
        }

    if task_id and "cancel" in lowered:
        cancelled = cancel_task(task_id)
        if cancelled is None:
            return {
                "status": "completed",
                "route": "thinkpad",
                "response": f"Cancel could not be applied to task #{task_id}. Only pending or failed tasks can be cancelled safely.",
                "result": {"action": "cancel", "taskId": task_id},
                "executedAt": utc_now(),
            }
        return {
            "status": "completed",
            "route": "thinkpad",
            "targetNode": "thinkpad",
            "response": f"Task #{task_id} is now cancelled.",
            "result": cancelled,
            "executedAt": utc_now(),
        }

    if any(keyword in lowered for keyword in ("status", "health", "queue", "load", "system")):
        return {
            "status": "completed",
            "route": "thinkpad",
            "targetNode": "thinkpad",
            "response": format_queue_summary(snapshot),
            "result": snapshot,
            "executedAt": utc_now(),
        }

    if "recent" in lowered and "task" in lowered:
        tasks = snapshot.get("tasks") or []
        if not tasks:
            response = "No recent tasks are recorded in the scheduler yet."
        else:
            response = "Recent tasks:\n" + "\n".join(
                f"- #{task.get('id')} {task.get('status', 'unknown')} :: {task.get('type', 'task')}"
                for task in tasks[:5]
            )
        return {
            "status": "completed",
            "route": "thinkpad",
            "targetNode": "thinkpad",
            "response": response,
            "result": {"tasks": tasks},
            "executedAt": utc_now(),
        }

    llm_response = invoke_llm(task_text, context, snapshot)
    if llm_response:
        return {
            "status": "completed",
            "route": "thinkpad",
            "targetNode": "thinkpad",
            "response": llm_response,
            "result": {"mode": "llm", "snapshot": snapshot},
            "executedAt": utc_now(),
        }

    return {
        "status": "completed",
        "route": "thinkpad",
        "targetNode": "thinkpad",
        "response": (
            "Command interface live. I can report system status, retry or cancel queue items, "
            "restart the backend, and route heavy jobs to the worker when it is available."
        ),
        "result": {"mode": "rule_based", "snapshot": snapshot},
        "executedAt": utc_now(),
    }


def execute_remote_worker(task_text: str, context: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if not execution_enabled():
        audit_security_event(
            "execution_blocked",
            {
                "action": "remote_dispatch",
                "reason": "execution_disabled",
            },
        )
        return {
            "status": "blocked",
            "route": "thinkpad",
            "targetNode": "thinkpad",
            "response": "Execution is disabled. Remote dispatch was blocked by safety policy.",
            "result": {"action": "remote_dispatch", "blocked": True, "reason": "execution_disabled"},
            "executedAt": utc_now(),
        }

    dispatch = dispatch_task(
        task_text,
        task_type=classify_task(task_text, params),
        preferred_node=params.get("preferredNode"),
    )
    url = dispatch.get("endpoint")
    if dispatch.get("targetNode") == "thinkpad":
        raise RuntimeError("No healthy remote worker available")
    if not url:
        raise RuntimeError("Worker API URL is not configured")

    payload = {"task": task_text, "context": context, "params": params}
    headers = signed_headers(payload)
    audit_security_event(
        "remote_dispatch_started",
        {
            "targetNode": dispatch.get("targetNode"),
            "endpoint": url,
            "reason": dispatch.get("reason"),
            "score": dispatch.get("score"),
        },
    )

    response = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=float(os.environ.get("ANDIE_NUC_WORKER_TIMEOUT_SECONDS", "30")),
    )
    response.raise_for_status()
    payload = response.json()
    worker_result = payload.get("worker") or {}
    message = payload.get("result") or payload.get("response") or "Remote worker completed the request."
    audit_security_event(
        "remote_dispatch_completed",
        {
            "targetNode": dispatch.get("targetNode"),
            "status": payload.get("status"),
            "workerStatus": worker_result.get("status"),
        },
    )
    return {
        "status": "completed",
        "route": dispatch.get("targetNode") or "nuc",
        "targetNode": dispatch.get("targetNode") or "nuc",
        "dispatchReason": dispatch.get("reason"),
        "dispatchScore": dispatch.get("score"),
        "rankedCandidates": dispatch.get("rankedCandidates") or [],
        "response": f"Heavy task routed to remote worker.\n\n{message}",
        "result": payload,
        "worker": worker_result,
        "executedAt": utc_now(),
    }


def run_command_interface(
    task_text: str,
    context: str,
    params: Dict[str, Any] | None,
    restart_shell_command: str,
) -> Dict[str, Any]:
    normalized_params = dict(params or {})
    if is_heavy_task(task_text, normalized_params):
        try:
            return execute_remote_worker(task_text, context, normalized_params)
        except Exception as exc:
            fallback = execute_local_command(task_text, context, normalized_params, restart_shell_command)
            fallback["workerDispatchError"] = str(exc)
            fallback["targetNode"] = "thinkpad"
            fallback["response"] = (
                "Worker dispatch was unavailable, so the command ran locally instead.\n\n"
                f"{fallback['response']}"
            )
            return fallback

    return execute_local_command(task_text, context, normalized_params, restart_shell_command)


async def handle_task(
    task_text: str,
    context: str = "",
    params: Dict[str, Any] | None = None,
    restart_shell_command: str = "",
) -> Dict[str, Any]:
    normalized_params = dict(params or {})
    lowered = (task_text or "").lower().strip()

    if "workflow" in lowered:
        if not execution_enabled():
            audit_security_event(
                "execution_blocked",
                {
                    "action": "workflow_start",
                    "reason": "execution_disabled",
                },
            )
            return {
                "type": "workflow",
                "status": "blocked",
                "task": task_text,
                "route": "thinkpad",
                "targetNode": "thinkpad",
                "response": "Execution is disabled. Workflow launch was blocked by safety policy.",
                "result": {
                    "task": task_text,
                    "streaming": False,
                    "blocked": True,
                    "reason": "execution_disabled",
                },
                "executedAt": utc_now(),
            }

        workflow_id = normalized_params.get("workflowId") or str(uuid4())
        asyncio.create_task(
            workflow_engine.run_workflow_stream(
                task=task_text,
                workflow_id=workflow_id,
                steps=normalized_params.get("workflowSteps"),
                context_text=context,
                memory=normalized_params.get("memory"),
                allow_recovery=bool(normalized_params.get("allowRecovery")),
            )
        )
        return {
            "type": "workflow",
            "status": "started",
            "task": task_text,
            "workflowId": workflow_id,
            "route": "thinkpad",
            "targetNode": "thinkpad",
            "response": f"Workflow started for '{task_text}'. Live updates are streaming now.",
            "result": {
                "workflowId": workflow_id,
                "task": task_text,
                "streaming": True,
            },
            "executedAt": utc_now(),
        }

    return run_command_interface(task_text, context, normalized_params, restart_shell_command)