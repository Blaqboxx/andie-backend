class Planner:
    def create_plan(self, input_data, context):
        return {
            "task": input_data,
            "context": context
        }

    def execute(self, plan):
        # placeholder logic
        return f"Executed: {plan['task']}"
