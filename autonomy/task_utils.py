def initialize_task(task):
    return {
        "id": task.get("id"),
        "action": task.get("action"),
        "params": task.get("params", {}),
        "status": "pending",   # pending | running | done | failed
        "retries": 0,
        "result": None,
        "error": None,
        "last_updated": None
    }
