"""Compatibility shim for legacy scheduler.queue imports.

Workflow and runtime modules historically imported helper functions from
scheduler.queue. The canonical queue implementation now lives in
orchestration.queue and exposes a class-based API.

Temporary Compatibility Layer:
- Keep legacy function names stable for older callers/tests.
- Delegate to the new queue backend whenever possible.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from orchestration.queue import BoundedTaskQueue, TaskStatus, create_task_queue

# Process-wide queue instance for legacy helper access.
_queue: BoundedTaskQueue = create_task_queue()


def queue_metrics() -> Dict[str, Any]:
    """Return current queue metrics using the new queue backend."""
    return _queue.get_metrics()


def recent_tasks(limit: int = 3) -> List[Dict[str, Any]]:
    """Return recent tasks in the legacy dict shape expected by APIs/tests."""
    tasks = _queue.get_all_tasks()
    tasks = sorted(tasks, key=lambda t: t.created_at or "", reverse=True)
    output: List[Dict[str, Any]] = []
    for task in tasks[: max(0, int(limit))]:
        output.append(
            {
                "id": task.id,
                "status": task.status.value if isinstance(task.status, TaskStatus) else str(task.status),
                "type": task.type.value if hasattr(task.type, "value") else str(task.type),
                "assignedNode": task.claimed_by_worker,
                "createdAt": task.created_at,
            }
        )
    return output


def cancel_task(task_id: Optional[str]) -> Dict[str, Any]:
    """Legacy cancel helper returning a stable payload."""
    if not task_id:
        return {"cancelled": False, "task_id": task_id, "reason": "missing_task_id"}
    task = _queue.cancel_task(str(task_id))
    return {
        "cancelled": bool(task),
        "task_id": str(task_id),
        "status": task.status.value if task else None,
    }


def request_manual_retry(task_id: Optional[str], preferred_node: Optional[str] = None) -> Dict[str, Any]:
    """Legacy retry helper for failed/dead-letter tasks."""
    if not task_id:
        return {"retried": False, "task_id": task_id, "reason": "missing_task_id"}

    task = _queue.get_task(str(task_id))
    if task is None:
        return {"retried": False, "task_id": str(task_id), "reason": "not_found"}

    if preferred_node:
        task.payload["preferredNode"] = preferred_node

    if task.status == TaskStatus.DEAD_LETTER:
        replayed = _queue.replay_dead_letter(task.id)
        if replayed is None:
            return {"retried": False, "task_id": str(task_id), "reason": "replay_failed"}
        return {"retried": True, "task_id": task.id, "status": replayed.status.value, "preferredNode": preferred_node}

    if task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.COMPLETED}:
        task.status = TaskStatus.PENDING
        task.error = None
        task.dead_letter_reason = None
        task.completed_at = None
        task.retry.last_error = None
        task.retry.retry_count = 0
        task.clear_claim()
        _queue._save_to_storage()  # noqa: SLF001 - compatibility mutation path
        return {"retried": True, "task_id": task.id, "status": task.status.value, "preferredNode": preferred_node}

    return {"retried": False, "task_id": task.id, "reason": f"status_{task.status.value}_not_retryable"}


def reroute_tasks(source: Optional[str], target: Optional[str], limit: int = 100) -> Dict[str, Any]:
    """Legacy reroute helper for self-healing path.

    We annotate pending tasks with a preferred node for downstream dispatch.
    """
    source_node = str(source or "")
    target_node = str(target or "")
    if not source_node or not target_node:
        return {"source": source_node, "target": target_node, "rerouted": 0, "task_ids": []}

    candidates = _queue.get_all_tasks()
    rerouted_ids: List[str] = []

    for task in candidates:
        if len(rerouted_ids) >= max(0, int(limit)):
            break
        if task.status != TaskStatus.PENDING:
            continue
        current_node = str(task.payload.get("preferredNode") or task.claimed_by_worker or "")
        if current_node and current_node != source_node:
            continue
        task.payload["preferredNode"] = target_node
        rerouted_ids.append(task.id)

    if rerouted_ids:
        _queue._save_to_storage()  # noqa: SLF001 - compatibility mutation path

    return {
        "source": source_node,
        "target": target_node,
        "rerouted": len(rerouted_ids),
        "task_ids": rerouted_ids,
    }


__all__ = [
    "queue_metrics",
    "recent_tasks",
    "cancel_task",
    "request_manual_retry",
    "reroute_tasks",
]
