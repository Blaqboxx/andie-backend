import heapq
import time
from core.goal_scorer import score_goal

class Goal:
    def __init__(self, description, priority=5):
        self.description = description
        self.priority = priority
        self.timestamp = time.time()

    def __lt__(self, other):
        # Lower number = higher priority
        if self.priority == other.priority:
            return self.timestamp < other.timestamp
        return self.priority < other.priority

class GoalManager:
    def __init__(self):
        self.queue = []

    def add_goal(self, goal: str):
        priority = score_goal(goal)
        goal_obj = Goal(goal, priority)
        heapq.heappush(self.queue, goal_obj)

    def get_next_goal(self):
        if self.queue:
            return heapq.heappop(self.queue)
        return None
