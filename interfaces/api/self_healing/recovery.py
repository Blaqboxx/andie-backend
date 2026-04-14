from __future__ import annotations

import time
from typing import Any, Dict

from interfaces.api.workflow_engine import workflow_engine
from scheduler.queue import reroute_tasks


def recovery_task_for_issue(issue: Dict[str, Any]) -> str | None:
    issue_type = issue.get("type")
    if issue_type == "queue_stuck":
        return "run process optimization workflow"
    if issue_type == "agent_failure":
        return "run recovery workflow"
    if issue_type == "high_cpu":
        return "run load balancing workflow"
    if issue_type == "node_failure":
        node = issue.get("node") or "worker"
        return f"reroute tasks from {node} to thinkpad"
    return None


async def recover(
    issue: Dict[str, Any],
    iteration: int,
    state: Dict[str, Any],
    workflow_id: str | None = None,
) -> Dict[str, Any]:
    task = recovery_task_for_issue(issue)
    if not task:
        return {"status": "no_action", "issue": issue, "task": None}

    if issue.get("type") == "node_failure":
        reroute_result = reroute_tasks(issue.get("node") or "nuc", "thinkpad")
        return {
            "status": "recovery_started",
            "issue": issue,
            "task": task,
            "workflowId": None,
            "workflow": {
                "task": task,
                "evaluation": {"status": "ok"},
                "response": f"Rerouted {reroute_result.get('rerouted', 0)} pending task(s) to thinkpad.",
                "result": reroute_result,
            },
        }

    workflow_id = workflow_id or f"self-heal-{issue.get('type', 'issue')}-{int(time.time() * 1000)}"
    result = await workflow_engine.run_workflow_stream(
        task=task,
        workflow_id=workflow_id,
        context_text="Self-healing recovery",
        memory={
            "source": "self_healing",
            "iteration": iteration,
            "issue": issue,
            "state": state,
        },
        allow_recovery=False,
    )
    return {
        "status": "recovery_started",
        "issue": issue,
        "task": task,
        "workflowId": workflow_id,
        "workflow": result,
    }