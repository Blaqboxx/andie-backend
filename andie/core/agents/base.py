class BaseAgent:
    def __init__(self, role):
        self.role = role

    def run(self, state):
        raise NotImplementedError
