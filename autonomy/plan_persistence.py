import os
import json

class PlanPersistence:
    def __init__(self, persist_path=None):
        self.persist_path = persist_path or os.path.join(os.getcwd(), "active_plan.json")

    def save(self, plan):
        with open(self.persist_path, "w") as f:
            json.dump(plan, f, default=str)

    def load(self):
        if not os.path.exists(self.persist_path):
            return None
        with open(self.persist_path, "r") as f:
            return json.load(f)

    def clear(self):
        if os.path.exists(self.persist_path):
            os.remove(self.persist_path)
