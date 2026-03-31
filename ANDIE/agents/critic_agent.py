class Critic:
    def review(self, step_desc, result):
        # Simple critic logic: flag errors or suggest improvements
        if result.get("status") != "SUCCESS":
            return f"Critic: Step '{step_desc}' failed. Suggest retry or review code."
        if "error" in str(result).lower():
            return f"Critic: Error detected in result for '{step_desc}'."
        return f"Critic: Step '{step_desc}' executed successfully."
