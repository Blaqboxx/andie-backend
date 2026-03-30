class Planner:

    def create_plan(self, goal: str):
        goal = goal.lower()

        if "data" in goal:
            return [
                {"step": 1, "description": "generate sample data"},
                {"step": 2, "description": "process the data"},
                {"step": 3, "description": "print results"}
            ]

        if "loop" in goal:
            return [
                {"step": 1, "description": "create a loop"},
                {"step": 2, "description": "print output"}
            ]

        return [{"step": 1, "description": goal}]
