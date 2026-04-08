""" brain/llm_engine.py"""

from brain.llm_router import llm_invoke

print("✅ LLM ENGINE ACTIVE")

def think(task, context=None):
    if context is None:
        context = []
    return llm_invoke(task, context)
