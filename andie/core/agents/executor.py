from .base import BaseAgent

class ExecutorAgent(BaseAgent):
    def __init__(self, trigger):
        super().__init__("executor")
        self.trigger = trigger

    def run(self, state):
        for task in state.plan.get("tasks", []):
            if task["status"] != "pending":
                continue
            try:
                result = self.trigger.execute(task)
                task["result"] = result
                task["status"] = "done"
            except Exception as e:
                task["error"] = str(e)
                task["status"] = "failed"
                task["retries"] += 1
        return state
