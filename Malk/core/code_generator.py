from openai import OpenAI

client = OpenAI()

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

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    code = response.choices[0].message.content.strip()

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
