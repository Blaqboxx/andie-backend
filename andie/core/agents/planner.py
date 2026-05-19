from .base import BaseAgent

class PlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__("planner")

    def run(self, state):
        # If a strategic shift is requested, generate a fundamentally different plan
        if state.metadata.get("strategy_shift"):
            plan = self._generate_alternative_strategy(state)
            state.plan = plan
            state.metadata.pop("strategy_shift", None)
            return state
        if state.plan and state.plan.get("status") == "active":
            return state
        plan = self._generate_plan(state)
        state.plan = plan
        return state

    def _generate_plan(self, state):
        return state.metadata.get("reasoning_output", {}).get("plan", {})

    def _generate_alternative_strategy(self, state):
        # Use goal context to generate a fundamentally different approach
        goal = state.metadata.get("goal", {})
        context = {
            "goal": goal,
            "failed_plans": goal.get("plans", []),
            "performance": goal.get("score", 0.0),
            "instruction": "generate a fundamentally different approach"
        }
        # This is a placeholder for your actual alternative strategy logic
        # In a real system, this would call out to an LLM or reasoning engine
        # For now, just mark the plan as 'alternative' for demonstration
        alt_plan = {
            "tasks": [{"action": "alternative_strategy", "params": {}, "status": "pending"}],
            "status": "active",
            "goal": goal,
            "note": "Generated alternative strategy"
        }
        # Optionally, append to failed_plans
        goal.setdefault("plans", []).append(state.plan)
        return alt_plan
