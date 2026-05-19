
import datetime

class Agent:
    def __init__(self, name):
        self.name = name

    def handle(self, task, context):
        t = task.lower().strip()
        if self.name == "Alpha":
            if "time" in t:
                now = datetime.datetime.now().strftime("%H:%M:%S")
                return f"The current time is {now}."
            if "date" in t:
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                return f"Today's date is {today}."
        if self.name == "Gamma":
            return "Gamma says: Why did the agent cross the wire? To get to the other protocol!"
        return f"Agent {self.name} handled task: {task}"


class AgentRouter:
    def __init__(self, agents=None):
        if agents is None:
            agents = [Agent("Alpha"), Agent("Beta"), Agent("Gamma")]
        self.agents = agents

    def route(self, task, context):
        t = task.lower()
        if "beta" in t:
            return self.agents[1].handle(task, context)
        if "gamma" in t or "joke" in t:
            return self.agents[2].handle(task, context)
        return self.agents[0].handle(task, context)
