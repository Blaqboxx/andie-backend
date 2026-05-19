from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/state")
def get_autonomy_state(request: Request):
    memory = getattr(request.app.state, "memory_service", None)
    if memory is None:
        return {"error": "Memory service not initialized"}
    try:
        # Compatibility shim for legacy paths that expected retrieve().
        if hasattr(memory, "retrieve"):
            traces = memory.retrieve({"type": "execution_trace"})
        else:
            traces = [
                e for e in memory.get_recent(200)
                if isinstance(e, dict) and e.get("type") == "execution_trace"
            ]

        if not traces:
            return {"status": "no_data"}

        latest = traces[-1]
        plan = latest.get("plan", {})
        tasks = plan.get("tasks", [])

        # progress calc
        completed = sum(1 for t in tasks if t.get("status") == "done")
        total = len(tasks) or 1
        progress = completed / total

        # current task
        current_task = next(
            (t for t in tasks if t.get("status") in ["pending", "running"]),
            None
        )

        return {
            "goal": latest.get("goal"),
            "plan_id": plan.get("id"),
            "plan_status": plan.get("status"),
            "progress": progress,
            "current_task": current_task,
            "last_decision": latest.get("decision"),
            "confidence": latest.get("confidence", 0.0)
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/decision/latest")
def get_latest_decision():
    return {
        "decision": "idle",
        "confidence": 0.0,
        "timestamp": "now"
    }

# --- DECISION HISTORY ---
@router.get("/decision/history")
def get_decision_history(limit: int = 10):
    return {
        "history": [],
        "count": 0
    }

# --- LOGS ---
@router.get("/logs")
def get_logs(tail: int = 10, compact: bool = False):
    return {
        "logs": [],
        "count": 0
    }

# --- GUARDRAILS ---
@router.get("/guardrails")
def get_guardrails():
    return {
        "status": "active",
        "rules": []
    }
