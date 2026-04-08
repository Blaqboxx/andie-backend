from Malk.core.memory_manager import get_recent_memory

""" If you have OpenAI or another LLM client, import and configure here"""
try:
    from openai import OpenAI
    client = OpenAI()
except ImportError:
    client = None

def decide_goal():
    memory = get_recent_memory(5) if 'get_recent_memory' in globals() else []
    prompt = f"Recent memory: {memory}\nWhat should I improve next?"
    if client:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an autonomous AI improving your system."},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content
    # Fallback: simple logic if LLM not available
    if not memory:
        return "Initialize system and test execution"
    last = memory[-1]
    if "error" in str(last):
        return "Fix previous error"
    return "Improve system performance"
