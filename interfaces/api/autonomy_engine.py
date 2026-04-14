from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict

import psutil

from interfaces.api.event_bus import emit_event, recent_events
from interfaces.api.node_monitor import check_node_health
from interfaces.api.self_healing import detect_issues, recover, recovery_task_for_issue, verify_recovery
from interfaces.api.workflow_engine import workflow_engine
from scheduler.queue import queue_metrics


LOOP_INTERVAL_SECONDS = float(os.environ.get("ANDIE_AUTONOMY_INTERVAL_SECONDS", "5"))
MAX_ITERATIONS = int(os.environ.get("ANDIE_AUTONOMY_MAX_ITERATIONS", "1000"))
CPU_RECOVERY_THRESHOLD = float(os.environ.get("ANDIE_AUTONOMY_CPU_THRESHOLD", "80"))
QUEUE_OPTIMIZATION_THRESHOLD = int(os.environ.get("ANDIE_AUTONOMY_QUEUE_THRESHOLD", "5"))
DECISION_COOLDOWN_SECONDS = float(os.environ.get("ANDIE_AUTONOMY_DECISION_COOLDOWN_SECONDS", "15"))
QUEUE_STABLE_RESET_THRESHOLD = int(os.environ.get("ANDIE_AUTONOMY_QUEUE_STABLE_RESET_THRESHOLD", "2"))
MAX_RECOVERY_ATTEMPTS = int(os.environ.get("ANDIE_SELF_HEAL_MAX_RECOVERY_ATTEMPTS", "3"))
RECENT_EVENT_LIMIT = int(os.environ.get("ANDIE_SELF_HEAL_EVENT_WINDOW", "20"))

RUNNING = False
THREAD: threading.Thread | None = None
STATE_LOCK = threading.Lock()
STOP_EVENT = threading.Event()
ITERATION_COUNT = 0
LAST_STATE: Dict[str, Any] | None = None
LAST_DECISION: str | None = None
LAST_DECISION_TIME = 0.0
LAST_ERROR: str | None = None
RECOVERY_ATTEMPTS: Dict[str, int] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(payload: Dict[str, Any]) -> None:
    event = {"updatedAt": utc_now(), **payload}
    asyncio.run(emit_event(event))


def get_system_state() -> Dict[str, Any]:
    queue = queue_metrics()
    previous_queue = (LAST_STATE or {}).get("queue", 0) if LAST_STATE else 0
    nodes = check_node_health()
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "memory": psutil.virtual_memory().percent,
        "queue": queue.get("pending", 0),
        "queueDelta": queue.get("pending", 0) - previous_queue,
        "running": queue.get("running", 0),
        "failed": queue.get("failed", 0),
        "nodes": nodes,
    }


def issue_signature(issue: Dict[str, Any]) -> str:
    parts = [issue.get("type") or "unknown"]
    if issue.get("agent"):
        parts.append(str(issue.get("agent")))
    if issue.get("node"):
        parts.append(str(issue.get("node")))
    return ":".join(parts)


def commit_decision(decision: str | None, now: float | None = None) -> None:
    global LAST_DECISION, LAST_DECISION_TIME

    if not decision:
        return
    LAST_DECISION = decision
    LAST_DECISION_TIME = now if now is not None else time.time()


def decide_action(state: Dict[str, Any]) -> str | None:
    global LAST_DECISION, LAST_DECISION_TIME

    now = time.time()

    if state.get("queue", 0) < QUEUE_STABLE_RESET_THRESHOLD and LAST_DECISION == "run process optimization workflow":
        LAST_DECISION = None

    if state.get("cpu", 0) < CPU_RECOVERY_THRESHOLD and state.get("failed", 0) == 0 and LAST_DECISION == "run recovery workflow":
        LAST_DECISION = None

    if now - LAST_DECISION_TIME < DECISION_COOLDOWN_SECONDS:
        return None

    decision = None
    if state.get("cpu", 0) > CPU_RECOVERY_THRESHOLD or state.get("failed", 0) > 0:
        decision = "run recovery workflow"
    elif state.get("queue", 0) > QUEUE_OPTIMIZATION_THRESHOLD:
        decision = "run process optimization workflow"

    if not decision or decision == LAST_DECISION:
        return None

    commit_decision(decision, now)
    return decision


def get_autonomy_status() -> Dict[str, Any]:
    with STATE_LOCK:
        return _status_unlocked()


def _status_unlocked() -> Dict[str, Any]:
    return {
        "running": RUNNING,
        "threadAlive": bool(THREAD and THREAD.is_alive()),
        "iteration": ITERATION_COUNT,
        "maxIterations": MAX_ITERATIONS,
        "intervalSeconds": LOOP_INTERVAL_SECONDS,
        "decisionCooldownSeconds": DECISION_COOLDOWN_SECONDS,
        "maxRecoveryAttempts": MAX_RECOVERY_ATTEMPTS,
        "lastState": LAST_STATE,
        "lastDecision": LAST_DECISION,
        "lastDecisionTime": LAST_DECISION_TIME or None,
        "recoveryAttempts": dict(RECOVERY_ATTEMPTS),
        "lastError": LAST_ERROR,
    }


def autonomy_loop() -> None:
    global RUNNING, ITERATION_COUNT, LAST_STATE, LAST_ERROR

    _emit({"type": "autonomy_started", "status": "running", "state": get_autonomy_status()})

    while not STOP_EVENT.is_set():
        with STATE_LOCK:
            if not RUNNING:
                break
            ITERATION_COUNT += 1
            iteration = ITERATION_COUNT

        if iteration > MAX_ITERATIONS:
            with STATE_LOCK:
                RUNNING = False
            _emit(
                {
                    "type": "autonomy_stopped",
                    "status": "max_iterations_reached",
                    "iteration": iteration - 1,
                }
            )
            break

        try:
            state = get_system_state()
            with STATE_LOCK:
                LAST_STATE = state
                LAST_ERROR = None

            _emit({"type": "autonomy_tick", "iteration": iteration, "state": state})

            cycle_action = None
            issues = detect_issues(state, recent_events(RECENT_EVENT_LIMIT))
            for issue in issues:
                signature = issue_signature(issue)
                attempts = RECOVERY_ATTEMPTS.get(signature, 0)
                if attempts >= MAX_RECOVERY_ATTEMPTS:
                    _emit(
                        {
                            "type": "self_healing_failed",
                            "iteration": iteration,
                            "issue": issue,
                            "attempt": attempts,
                            "reason": "max_recovery_attempts_reached",
                        }
                    )
                    continue

                _emit(
                    {
                        "type": "self_healing_detected",
                        "iteration": iteration,
                        "issue": issue,
                        "attempt": attempts + 1,
                    }
                )
                if issue.get("type") == "node_failure":
                    _emit(
                        {
                            "type": "node_failure",
                            "iteration": iteration,
                            "node": issue.get("node"),
                            "status": issue.get("status"),
                        }
                    )

                RECOVERY_ATTEMPTS[signature] = attempts + 1
                recovery_task = recovery_task_for_issue(issue)
                workflow_id = None
                if recovery_task:
                    workflow_id = f"self-heal-{issue.get('type', 'issue')}-{int(time.time() * 1000)}"
                    commit_decision(recovery_task)
                _emit(
                    {
                        "type": "self_healing_recovery_started",
                        "iteration": iteration,
                        "issue": issue,
                        "attempt": RECOVERY_ATTEMPTS[signature],
                        "task": recovery_task,
                        "workflowId": workflow_id,
                    }
                )
                recovery_result = asyncio.run(recover(issue, iteration, state, workflow_id=workflow_id))

                new_state = get_system_state()
                with STATE_LOCK:
                    LAST_STATE = new_state

                if verify_recovery(new_state, issue):
                    RECOVERY_ATTEMPTS.pop(signature, None)
                    _emit(
                        {
                            "type": "self_healing_success",
                            "iteration": iteration,
                            "issue": issue,
                            "state": new_state,
                            "workflowId": recovery_result.get("workflowId"),
                        }
                    )
                else:
                    _emit(
                        {
                            "type": "self_healing_failed",
                            "iteration": iteration,
                            "issue": issue,
                            "attempt": RECOVERY_ATTEMPTS[signature],
                            "state": new_state,
                            "workflowId": recovery_result.get("workflowId"),
                        }
                    )
                cycle_action = recovery_task or cycle_action

            if issues:
                _emit(
                    {
                        "type": "autonomy_cycle_complete",
                        "iteration": iteration,
                        "decision": cycle_action,
                        "evaluation": None,
                    }
                )
                if STOP_EVENT.wait(LOOP_INTERVAL_SECONDS):
                    break
                continue

            decision = decide_action(state)

            workflow_result = None
            if decision:
                workflow_id = f"autonomy-{int(time.time() * 1000)}"
                _emit(
                    {
                        "type": "autonomy_decision",
                        "iteration": iteration,
                        "decision": decision,
                        "workflowId": workflow_id,
                    }
                )
                workflow_result = asyncio.run(
                    workflow_engine.run_workflow_stream(
                        task=decision,
                        workflow_id=workflow_id,
                        context_text="Autonomy loop decision",
                        memory={"source": "autonomy", "iteration": iteration, "state": state},
                        allow_recovery=False,
                    )
                )

            _emit(
                {
                    "type": "autonomy_cycle_complete",
                    "iteration": iteration,
                    "decision": decision,
                    "evaluation": workflow_result.get("evaluation") if workflow_result else None,
                }
            )
        except Exception as exc:
            with STATE_LOCK:
                LAST_ERROR = str(exc)
            _emit({"type": "autonomy_error", "iteration": iteration, "error": str(exc)})

        if STOP_EVENT.wait(LOOP_INTERVAL_SECONDS):
            break

    with STATE_LOCK:
        RUNNING = False


def start_autonomy() -> Dict[str, Any]:
    global RUNNING, THREAD, ITERATION_COUNT, LAST_ERROR, LAST_STATE, LAST_DECISION, LAST_DECISION_TIME, RECOVERY_ATTEMPTS

    with STATE_LOCK:
        if RUNNING:
            return {"status": "already_running", "autonomy": _status_unlocked()}
        RUNNING = True
        ITERATION_COUNT = 0
        LAST_ERROR = None
        LAST_STATE = None
        LAST_DECISION = None
        LAST_DECISION_TIME = 0.0
        RECOVERY_ATTEMPTS = {}
        STOP_EVENT.clear()
        THREAD = threading.Thread(target=autonomy_loop, name="andie-autonomy", daemon=True)
        THREAD.start()
        status = _status_unlocked()

    return {"status": "started", "autonomy": status}


def stop_autonomy() -> Dict[str, Any]:
    global RUNNING

    with STATE_LOCK:
        was_running = RUNNING
        RUNNING = False
        STOP_EVENT.set()
        status = _status_unlocked()

    if was_running:
        _emit({"type": "autonomy_stopped", "status": "stopped", "state": status})
    return {"status": "stopped", "autonomy": status}