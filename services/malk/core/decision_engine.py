

def decide_goal():
from andie_backend.brain.llm_router import call_llm

def decide_goal(request):
    try:
        memory = request.app.state.memory_service.get_recent(5)
    except Exception as e:
        print("⚠️ Memory access failed:", e)
        memory = []

    prompt = f"Recent memory: {memory}\nWhat should I improve next?"

    if memory:
        try:
            response = call_llm(
                prompt,
                system="You are ANDIE, an autonomous AI improving your system.",
                context=None,
                model="gpt-4o"
            )
            return response.strip() if isinstance(response, str) else str(response)
        except Exception as e:
            print("⚠️ LLM failed:", e)

    if not memory:
        return "Initialize system and test execution"

    if "error" in str(memory[-1]).lower():
        return "Fix previous error"

    return "Improve system performance"
def decide_goal(request):
    try:
        memory_service = request.app.state.memory_service
        memory = memory_service.get_recent(5)
        context = memory_service.build_context(memory)
    except Exception as e:
        print("⚠️ Memory access failed:", e)
        memory = []
        context = ""

    prompt = f"""
{context}
What should I improve next?
"""

    if memory:
        try:
            response = call_llm(
                prompt,
                system="You are ANDIE, an autonomous AI improving your system.",
                context=None,
                model="gpt-4o"
            )
            return response.strip() if isinstance(response, str) else str(response)
        except Exception as e:
            print("⚠️ LLM failed:", e)

    if not memory:
        return "Initialize system and test execution"

    if "error" in str(memory[-1]).lower():
        return "Fix previous error"

    return "Improve system performance"
