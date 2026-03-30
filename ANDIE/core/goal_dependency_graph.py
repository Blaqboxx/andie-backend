class GoalNode:
    def __init__(self, description, priority=5):
        self.description = description
        self.priority = priority
        self.dependencies = []  # List of GoalNode
        self.completed = False

    def add_dependency(self, node):
        self.dependencies.append(node)

    def is_ready(self):
        return all(dep.completed for dep in self.dependencies)

class GoalDependencyGraph:
    def __init__(self):
        self.nodes = []

    def add_goal(self, description, priority=5, dependencies=None):
        node = GoalNode(description, priority)
        if dependencies:
            for dep in dependencies:
                node.add_dependency(dep)
        self.nodes.append(node)
        return node

    def get_ready_goals(self):
        return [node for node in self.nodes if not node.completed and node.is_ready()]

    def mark_completed(self, node):
        node.completed = True
