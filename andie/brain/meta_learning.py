class MetaLearningEngine:
    def __init__(self, memory):
        self.memory = memory

    def aggregate(self):
        records = self.memory.retrieve({"type": "execution_trace"})
        stats = {}
        for r in records:
            for t in r.get("tasks", []):
                key = (t["action"], r.get("goal"))
                if key not in stats:
                    stats[key] = {"success": 0, "total": 0}
                stats[key]["total"] += 1
                if t["status"] == "done":
                    stats[key]["success"] += 1
        # Store aggregated strategies
        for (action, goal), v in stats.items():
            score = v["success"] / (v["total"] or 1)
            self.memory.store({
                "type": "strategy_score",
                "action": action,
                "goal": goal,
                "score": score
            })
