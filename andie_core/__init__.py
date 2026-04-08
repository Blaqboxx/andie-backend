

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
            "agent": agent,
            "tool": tool,
            "message": res,
            "data": {},
        }
    if isinstance(res, dict):
        return {
            "status": res.get("status", "success"),
            "agent": res.get("agent", agent),
            "tool": res.get("tool", tool),
            "message": res.get("message", ""),
            "data": res.get("data", {}),
        }
    return {
        "status": "error",
        "agent": agent,
        "tool": tool,
        "message": "Invalid response format",
        "data": {},
    }

class AndieCore:
    def __init__(self):
        self.memory = ShortTermMemory()
        self.planner = Planner()
        self.evaluator = Evaluator()
        self.router = AgentRouter()

    def run(self, input_data, agent=None):
        # --- Memory Recall: inject relevant context ---
        recalled = self.memory.recall(input_data, n=6)
        context_summary = build_context(recalled, k=3)
        context = self.memory.get_context()

        # --- Meta-controller: LLM decides tool ---
        tool = llm_decide_tool(input_data, context_summary)

        # --- Planner (optional, can be expanded) ---
        plan = self.planner.create_plan(input_data, context)

        # --- Route/Execute ---
        agent_name = agent if agent else getattr(self.router.agents[0], "name", "unknown")
        result = None
        if tool == "llm":
            # Fallback to LLM direct response
            result = think(input_data)
        elif tool == "memory":
            # Memory tool
            from tools.registry import memory_tool
            result = memory_tool(input_data)
        elif tool in ["run_command", "open_browser"]:
            # Route to tool via agent (could be expanded for multi-agent)
            # Find agent by name if provided
            agent_obj = None
            if agent:
                for a in self.router.agents:
                    if a.name.lower() == agent.lower():
                        agent_obj = a
                        break
            if not agent_obj:
                agent_obj = self.router.agents[0]
            result = agent_obj.handle(plan["task"], plan["context"])
            agent_name = agent_obj.name
        else:
            # Unknown tool, fallback
            result = think(input_data)

        self.evaluator.evaluate(result)
        norm = normalize_response(result, agent=agent_name, tool=tool)
        # --- Memory Write: store structured memory ---
        self.memory.update(input_data, agent_name, tool, norm)
        # Return both result and context for UI
        return {"result": norm, "context": self.memory.get_context()}

    def recall_memory(self, query, n=5):
        return self.memory.recall(query, n=n)
