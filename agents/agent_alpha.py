import json
from brain.llm_engine import think
from tools.executor import execute

def safe_parse_llm_output(output):
    try:
        return json.loads(output)
    except Exception:
        return {
            "type": "text",
            "content": output
        }

def normalize_llm_output(parsed):
    if isinstance(parsed, dict) and "action" in parsed:
        return {
            "type": "tool",
            "action": parsed.get("action"),
            "input": parsed.get("input", "")
        }
    return {
        "type": "text",
        "content": parsed if isinstance(parsed, str) else str(parsed)
    }

def run_agent(task, memory=None):
    if memory is None:
        memory = []

    raw_output = think(task, memory)
    print(f"[DEBUG] Raw LLM Output: {raw_output}")

    parsed = safe_parse_llm_output(raw_output)
    result = normalize_llm_output(parsed)
    print(f"[DEBUG] Normalized: {result}")

    # CASE 1: TOOL CALL
    if result["type"] == "tool":
        action = result["action"]
        if not action:
            print("[WARN] No action provided, falling back to text")
            return normalize_response(raw_output, agent="alpha", tool="unknown")
        tool_result = execute(action, result["input"])
        return normalize_response(tool_result, agent="alpha", tool=action)
    # CASE 2: DIRECT RESPONSE
    return normalize_response(result["content"], agent="alpha", tool="llm")
