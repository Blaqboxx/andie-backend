
from services.malk.core.memory_manager import get_recent_memory
from andie.brain.llm_router import call_llm

def decide_goal():
    memory = get_recent_memory(5) if 'get_recent_memory' in globals() else []
    prompt = f"Recent memory: {memory}\nWhat should I improve next?"
    if memory:
        system = "You are an autonomous AI improving your system."
        return call_llm(prompt, system=system, context=None, model="gpt-4o").strip()
    # Fallback: simple logic if LLM not available
    if not memory:
        return "Initialize system and test execution"
    last = memory[-1]
    if "error" in str(last):
        return "Fix previous error"
    return "Improve system performance"
