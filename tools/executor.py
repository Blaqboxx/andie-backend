from tools.registry import tools

def execute(action, input_data):
    if action not in tools:
        return {
            "status": "error",
            "error": f"Unknown tool: {action}",
            "input": input_data
        }
    try:
        result = tools[action](input_data)
        if not result:
            return {
                "status": "error",
                "error": f"Tool {action} returned no response",
                "input": input_data
            }
        return result
    except Exception as e:
        return {
            "status": "error",
            "error": f"Execution failed: {str(e)}",
            "input": input_data
        }
