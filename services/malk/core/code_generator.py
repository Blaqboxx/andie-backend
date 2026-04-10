def generate_code(task):
from andie.brain.llm_router import call_llm

def generate_code(task):
    prompt = f"""
You are a Python code generator.

ONLY return valid Python code.
NO explanations.
NO markdown.
NO comments unless necessary.
DO NOT use or import unavailable modules like 'timeout_decorator', 'requests', 'pip', or any external packages not in the Python standard library.
If the task requires unavailable modules, use only standard library alternatives or print a message instead.

Task:
{task}

Return ONLY executable Python.
"""
    code = call_llm(prompt, system=None, context=None, model="gpt-4o").strip()
    # CLEAN SAFETY FILTER
    if "```" in code:
        code = code.replace("```python", "").replace("```", "").strip()
    # --- Validate Python syntax ---
    import ast
    try:
        ast.parse(code)
    except Exception as e:
        # If not valid Python, return a safe stub
        return "print('Error: Invalid code generated')"
    return code

    return code
