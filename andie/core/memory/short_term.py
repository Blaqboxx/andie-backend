import time
import uuid

class ShortTermMemory:
    def __init__(self):
        self.history = []  # List of memory dicts

    def get_context(self, query=None, n=5):
        if query:
            query = query.lower()
            matches = [m for m in self.history if query in m["task"].lower() or query in m["result"].lower()]
            return matches[-n:]
        return self.history[-n:]

    def update(self, task, agent, tool, response):
        self.history.append({
            "id": str(uuid.uuid4()),
            "task": task,
            "agent": agent,
            "tool": tool,
            "result": response["message"],
            "status": response["status"],
            "timestamp": time.time()
        })

    def recall(self, query, n=5):
        query = query.lower()
        results = [m for m in self.history if query in m["task"].lower() or query in m["result"].lower()]
        return results[-n:]
