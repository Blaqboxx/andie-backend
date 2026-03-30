import os
import random
from openai import OpenAI

SYSTEM_PROMPT = """
You are ANDIE's autonomous goal generator.
Generate a useful, safe, and achievable goal for an AI agent in a Python sandbox.
Return only the goal as a single line of text.
"""

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def generate_goal(recent_memory=None):
    # 30% chance to generate a self-repair goal
    repair_goals = [
        "fix error in data processing",
        "retry failed task",
        "analyze last failure and generate fix",
        "improve error handling in code"
    ]
    if random.random() < 0.3:
        return random.choice(repair_goals)
    memory_context = f"Recent Memory: {recent_memory}" if recent_memory else ""
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": memory_context}
        ]
    )
    goal = response.choices[0].message.content.strip()
    return goal
