
import inspect
import sys
from andie.brain import llm_router
print("LLM ROUTER FILE:", inspect.getfile(llm_router))
for path in sys.path:
    print("PATH:", path)

import json
import requests
from brain.llm_engine import think
from tools.executor import execute

MEMORY_API_URL = "http://localhost:8000/memory"

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
    return {"type": "text", "content": parsed if isinstance(parsed, str) else str(parsed)}

def run_agent(task):
    # Query memory from centralized API
    try:
        resp = requests.post(f"{MEMORY_API_URL}/query", json={"query": "recent", "top_k": 10})
        resp.raise_for_status()
        memory = resp.json().get("results", [])
    except Exception:
        memory = []

    llm_input = {
        "prompt": task,
        "system": "You are an AI agent executing a task.",
        "context": str(memory),
        "metadata": {"agent": "agent_alpha"}
    }

    raw_output = think(llm_input)
    print(f"[DEBUG] Raw LLM Output: {raw_output}")

    parsed = safe_parse_llm_output(raw_output)
    result = normalize_llm_output(parsed)
    print(f"[DEBUG] Normalized: {result}")

    # CASE 1: TOOL CALL
    if result["type"] == "tool":
        action = result["action"]
        if not action:
            return {"status": "no_action", "result": result}
        # Simulate tool execution (placeholder)
        return {"status": "tool_executed", "action": action, "input": result.get("input", "")}
    # CASE 2: TEXT OUTPUT
    return {"status": "ok", "result": result}