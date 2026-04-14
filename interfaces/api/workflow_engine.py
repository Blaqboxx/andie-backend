from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

import psutil

from andie_core.logger import Logger
from interfaces.api.event_bus import emit_event
from scheduler.queue import queue_metrics, recent_tasks


DEFAULT_WORKFLOW_STEPS = [
    "health_agent",
    "process_agent",
    "recovery_agent",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize_recent_tasks(limit: int = 3) -> List[Dict[str, Any]]:
    tasks = recent_tasks(limit)
    return [
        {
            "id": task.get("id"),
            "status": task.get("status"),
            "type": task.get("type"),
            "assignedNode": task.get("assignedNode"),
        }
        for task in tasks
    ]


def check_process_running(process_name: str) -> bool:
    process_name = (process_name or "").lower()
    if not process_name:
        return False

    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (process.info.get("name") or "").lower()
            cmdline = " ".join(process.info.get("cmdline") or []).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if process_name in name or process_name in cmdline:
            return True
    return False


def run_health_step(context: Dict[str, Any]) -> Dict[str, Any]:
    cpu = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    queue = queue_metrics()

    llm_ready = True
    llm_error = None
    try:
        __import__("brain.llm_engine")
    except Exception as exc:
        llm_ready = False
        llm_error = str(exc)

    warnings: List[str] = []
    if cpu >= 90:
        warnings.append("cpu_high")
    if memory >= 90:
        warnings.append("memory_high")
    if disk >= 90:
        warnings.append("disk_high")
    if not llm_ready:
        warnings.append("llm_unavailable")

    return {
        "status": "warning" if warnings else "ok",
        "metrics": {
            "cpu": cpu,
            "memory": memory,
            "disk": disk,
            "queue": queue,
            "recentTasks": summarize_recent_tasks(),
            "llmReady": llm_ready,
        },
        "warnings": warnings,
        "detail": llm_error,
        "task": context.get("task"),
    }


def run_process_step(context: Dict[str, Any]) -> Dict[str, Any]:
    backend_process = os.environ.get("ANDIE_BACKEND_PROCESS_NAME", "uvicorn")
    scheduler_process = os.environ.get("ANDIE_SCHEDULER_PROCESS_NAME", "scheduler.py")
    worker_process = os.environ.get("ANDIE_WORKER_API_PROCESS_NAME", "worker_api.py")

    processes = {
        "backend": check_process_running(backend_process),
        "scheduler": check_process_running(scheduler_process),
        "workerApi": check_process_running(worker_process),
    }
    missing = [name for name, running in processes.items() if not running]

    return {
        "status": "warning" if missing else "ok",
        "processes": processes,
        "missing": missing,
        "lastHealth": (context.get("shared") or {}).get("health_agent"),
    }


def run_recovery_step(context: Dict[str, Any], allow_actions: bool = False) -> Dict[str, Any]:
    shared = context.get("shared") or {}
    health = shared.get("health_agent") or {}
    process = shared.get("process_agent") or {}
    recommendations: List[Dict[str, str]] = []

    for warning in health.get("warnings") or []:
        if warning == "cpu_high":
            recommendations.append({"issue": warning, "action": "reduce heavy workloads or route to worker node"})
        elif warning == "memory_high":
            recommendations.append({"issue": warning, "action": "restart memory-intensive services or drain queue"})
        elif warning == "disk_high":
            recommendations.append({"issue": warning, "action": "rotate logs or clear stale artifacts"})
        elif warning == "llm_unavailable":
            recommendations.append({"issue": warning, "action": "check model credentials and brain service imports"})

    for missing in process.get("missing") or []:
        action = "restart service"
        if missing == "backend":
            action = "restart backend"
        elif missing == "scheduler":
            action = "restart scheduler"
        elif missing == "workerApi":
            action = "restart worker api"
        recommendations.append({"issue": f"process_missing:{missing}", "action": action})

    executed_actions: List[str] = []
    if allow_actions:
        # Safety boundary: only record intent in Step 1. Actual autonomous recovery comes later.
        executed_actions = [item["action"] for item in recommendations]

    return {
        "status": "warning" if recommendations else "ok",
        "recommendations": recommendations,
        "executedActions": executed_actions,
        "mode": "active" if allow_actions else "advisory",
    }


STEP_HANDLERS = {
    "health_agent": run_health_step,
    "process_agent": run_process_step,
    "recovery_agent": run_recovery_step,
}


def run_step(step_name: str, context: Dict[str, Any], allow_recovery: bool = False) -> Dict[str, Any]:
    handler = STEP_HANDLERS.get(step_name)
    if handler is None:
        raise ValueError(f"Unknown workflow step: {step_name}")

    if step_name == "recovery_agent":
        output = handler(context, allow_actions=allow_recovery)
    else:
        output = handler(context)

    return {
        "agent": step_name,
        "status": output.get("status", "ok"),
        "output": output,
        "completedAt": utc_now(),
    }


def evaluate_workflow(results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    failed_steps: List[str] = []
    for result in results:
        if result.get("status") == "failed":
            failed_steps.append(result.get("agent", "unknown"))
        elif result.get("status") == "warning":
            warnings.append({
                "agent": result.get("agent"),
                "output": result.get("output"),
            })

    if failed_steps:
        overall = "failed"
    elif warnings:
        overall = "warning"
    else:
        overall = "ok"

    return {
        "status": overall,
        "warningCount": len(warnings),
        "failedSteps": failed_steps,
        "warnings": warnings,
    }


def format_workflow_response(task: str, steps: List[str], evaluation: Dict[str, Any]) -> str:
    route = " -> ".join(steps)
    status = evaluation.get("status", "ok")
    warning_count = evaluation.get("warningCount", 0)
    if status == "ok":
        return f"Workflow completed cleanly for '{task}'. Pipeline: {route}."
    if status == "failed":
        failed = ", ".join(evaluation.get("failedSteps") or []) or "unknown"
        return f"Workflow failed for '{task}'. Pipeline: {route}. Failed steps: {failed}."
    return f"Workflow completed with {warning_count} warning(s) for '{task}'. Pipeline: {route}."


class WorkflowEngine:
    def __init__(self):
        self.logger = Logger("WorkflowEngine")

    def run_workflow(
        self,
        task: str,
        steps: List[str] | None = None,
        context_text: str = "",
        memory: Dict[str, Any] | None = None,
        allow_recovery: bool = False,
    ) -> Dict[str, Any]:
        pipeline = list(steps or DEFAULT_WORKFLOW_STEPS)
        context: Dict[str, Any] = {
            "task": task,
            "context": context_text,
            "memory": dict(memory or {}),
            "shared": {},
            "last_result": None,
            "startedAt": utc_now(),
        }
        self.logger.info(f"workflow_start task={task} steps={pipeline}")

        results: List[Dict[str, Any]] = []
        for step_name in pipeline:
            self.logger.info(f"workflow_step_start step={step_name} task={task}")
            started_at = utc_now()
            try:
                result = run_step(step_name, context, allow_recovery=allow_recovery)
                result["startedAt"] = started_at
            except Exception as exc:
                result = {
                    "agent": step_name,
                    "status": "failed",
                    "output": {"error": str(exc)},
                    "startedAt": started_at,
                    "completedAt": utc_now(),
                }
            results.append(result)
            context["last_result"] = result
            context["shared"][step_name] = result.get("output")
            self.logger.info(f"workflow_step_done step={step_name} status={result.get('status')} task={task}")

            if result.get("status") == "failed":
                break

        evaluation = evaluate_workflow(results)
        response = format_workflow_response(task, pipeline, evaluation)
        workflow_result = {
            "task": task,
            "workflow": pipeline,
            "results": results,
            "evaluation": evaluation,
            "context": {
                "memory": context["memory"],
                "shared": context["shared"],
                "startedAt": context["startedAt"],
                "completedAt": utc_now(),
            },
            "response": response,
        }
        self.logger.info(f"workflow_complete task={task} status={evaluation.get('status')}")
        return workflow_result

    async def run_workflow_stream(
        self,
        task: str,
        workflow_id: str,
        steps: List[str] | None = None,
        context_text: str = "",
        memory: Dict[str, Any] | None = None,
        allow_recovery: bool = False,
    ) -> Dict[str, Any]:
        pipeline = list(steps or DEFAULT_WORKFLOW_STEPS)
        context: Dict[str, Any] = {
            "task": task,
            "context": context_text,
            "memory": dict(memory or {}),
            "shared": {},
            "last_result": None,
            "startedAt": utc_now(),
        }
        self.logger.info(f"workflow_start task={task} workflow_id={workflow_id} steps={pipeline}")
        await emit_event(
            {
                "type": "workflow_start",
                "workflowId": workflow_id,
                "task": task,
                "workflow": pipeline,
                "updatedAt": utc_now(),
            }
        )

        results: List[Dict[str, Any]] = []
        for step_name in pipeline:
            self.logger.info(f"workflow_step_start step={step_name} task={task} workflow_id={workflow_id}")
            await emit_event(
                {
                    "type": "workflow_step_start",
                    "workflowId": workflow_id,
                    "task": task,
                    "step": step_name,
                    "updatedAt": utc_now(),
                }
            )
            await asyncio.sleep(0)

            started_at = utc_now()
            try:
                result = run_step(step_name, context, allow_recovery=allow_recovery)
                result["startedAt"] = started_at
            except Exception as exc:
                result = {
                    "agent": step_name,
                    "status": "failed",
                    "output": {"error": str(exc)},
                    "startedAt": started_at,
                    "completedAt": utc_now(),
                }

            results.append(result)
            context["last_result"] = result
            context["shared"][step_name] = result.get("output")
            self.logger.info(
                f"workflow_step_done step={step_name} status={result.get('status')} task={task} workflow_id={workflow_id}"
            )
            await emit_event(
                {
                    "type": "workflow_step_complete",
                    "workflowId": workflow_id,
                    "task": task,
                    "step": step_name,
                    "result": result,
                    "updatedAt": utc_now(),
                }
            )
            await asyncio.sleep(0)

            if result.get("status") == "failed":
                break

        evaluation = evaluate_workflow(results)
        response = format_workflow_response(task, pipeline, evaluation)
        workflow_result = {
            "task": task,
            "workflowId": workflow_id,
            "workflow": pipeline,
            "results": results,
            "evaluation": evaluation,
            "context": {
                "memory": context["memory"],
                "shared": context["shared"],
                "startedAt": context["startedAt"],
                "completedAt": utc_now(),
            },
            "response": response,
        }
        self.logger.info(
            f"workflow_complete task={task} status={evaluation.get('status')} workflow_id={workflow_id}"
        )
        await emit_event(
            {
                "type": "workflow_complete",
                "workflowId": workflow_id,
                "task": task,
                "results": results,
                "evaluation": evaluation,
                "response": response,
                "updatedAt": utc_now(),
            }
        )
        return workflow_result


workflow_engine = WorkflowEngine()