from .base import BaseAgent

class AnalystAgent(BaseAgent):
    def __init__(self):
        super().__init__("analyst")

    def run(self, state):
        tasks = state.plan.get("tasks", [])
        failures = [t for t in tasks if t["status"] == "failed"]
        state.metadata["analysis"] = {
            "failure_count": len(failures),
            "total_tasks": len(tasks)
        }
        return state
