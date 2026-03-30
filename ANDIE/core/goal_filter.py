SAFE_KEYWORDS = [
    "print",
    "generate",
    "optimize",
    "analyze",
    "process"
]

BLOCKED_KEYWORDS = [
    "delete",
    "shutdown",
    "network",
    "system"
]

def is_safe_goal(goal: str):
    goal = goal.lower()
    for word in BLOCKED_KEYWORDS:
        if word in goal:
            return False
    return any(word in goal for word in SAFE_KEYWORDS)
