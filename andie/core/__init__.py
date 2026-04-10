

from .execution.planner import Planner
from .execution.agent_router import AgentRouter
from .memory.short_term import ShortTermMemory
from .evaluation.evaluator import Evaluator
from brain.llm_engine import think

def normalize_response(res, agent="unknown", tool="unknown"):
    def build_context(memories, k=3):
        trimmed = memories[-k:]
        return "\n".join([
            f"- [{m['agent']}/{m['tool']}] {m['result']}"
            for m in trimmed
        ])

    # Meta-controller: LLM-based tool selection
    def llm_decide_tool(task, context_summary):
        prompt = f"""
    You are ANDIE's meta-controller. Given the user task and recent memory, select the best tool to use.

    User Task: {task}

    Recent Memory:
    {context_summary}

    Available tools: run_command, open_browser, memory, llm

    Respond ONLY with the tool name (e.g., open_browser, run_command, memory, llm).
    """
        tool = think(prompt)
        # Defensive: extract tool name from LLM output
        tool = tool.strip().split()[0].lower()
        if tool not in ["run_command", "open_browser", "memory", "llm"]:
            return "llm"
        return tool
    if isinstance(res, str):
        return {
            "status": "success",
