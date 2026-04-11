from andie_core.agents.app_builder_agent import AppBuilderAgent

class AgentOrchestrator:
    def __init__(self):
        self.app_builder = AppBuilderAgent()

    def handle_task(self, task):
        if task["type"] == "scaffold":
            return self.app_builder.scaffold_app(task["app_name"], task.get("template", "python"))
        elif task["type"] == "build":
            return self.app_builder.build_app(task["app_name"])
        elif task["type"] == "deploy":
            return self.app_builder.deploy_app(task["app_name"])
        # ... other agent tasks
