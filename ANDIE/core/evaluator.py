def evaluate_result(result: dict):
    """
    Basic evaluation logic.
    """
    if result.get("status") == "SUCCESS":
        return {"success": True}
    return {"success": False, "reason": result}
