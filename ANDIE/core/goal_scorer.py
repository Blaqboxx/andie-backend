def score_goal(goal: str) -> int:
    """
    Lower score = higher priority
    """
    goal = goal.lower()
    # 🔥 RULE-BASED SCORING (v1)
    if "urgent" in goal or "now" in goal:
        return 1
    if "error" in goal or "fix" in goal:
        return 2
    if "optimize" in goal or "improve" in goal:
        return 3
    if "generate" in goal or "create" in goal:
        return 4
    return 5  # default
