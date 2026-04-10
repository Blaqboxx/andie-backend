""" brain/llm_engine.py"""

from andie.brain.llm_router import call_llm

print("✅ LLM ENGINE ACTIVE")

def think(llm_input):
    """
    Accepts a dict with keys: prompt, system, context, metadata, model.
    Calls the unified LLM gateway and returns the response.
    """
    prompt = llm_input.get("prompt")
    system = llm_input.get("system")
    context = llm_input.get("context")
    model = llm_input.get("model", "gpt-4o")
    return call_llm(prompt, system=system, context=context, model=model)
