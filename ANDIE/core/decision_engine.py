import os
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are ANDIE, an autonomous AI system.

Generate ONLY valid Python code.
- No explanations
- No markdown
- No imports unless absolutely necessary
- Keep code safe and simple
"""

FORBIDDEN = ["import", "open(", "__import__", "subprocess", "os.", "sys."]

def sanitize(code: str):
    for token in FORBIDDEN:
        if token in code:
            return 'print("Blocked unsafe pattern")'
    return code

def generate_code_from_goal(goal: str) -> str:
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Goal: {goal}"}
        ]
    )

    code = response.choices[0].message.content.strip()
    return sanitize(code)
