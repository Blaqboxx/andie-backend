from Malk.core.decision_engine import decide_goal
from Malk.core.planner import create_plan
from Malk.core.code_generator import generate_code
from Malk.agents.executor_agent import execute_task
from Malk.core.evaluator import evaluate_result
from Malk.core.memory_manager import store_result, store_error_pattern, find_similar_fix
from Malk.core.logger import log_event

MAX_TASKS = 5
MAX_RUNTIME = 10  # seconds
MAX_RETRIES = 2

""" Self-debugging suggestion using LLM"""
try:
    from openai import OpenAI
    client = OpenAI()
except ImportError:
    client = None

def suggest_fix(goal, plan, code, error):
    if not client or not error:
        return "No suggestion (LLM unavailable or no error)."
    prompt = f"""
You are an expert Python debugger. Given the following:
Goal: {goal}
Plan: {plan}
Code:
{code}
Error:
{error}
Suggest a fix in one sentence.
"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()

def run_autonomy_cycle():
    goal = decide_goal()
    plan = create_plan(goal)
    code = generate_code(plan)
    log_event(f"Generated code:\n{code}")
    retries = 0
    while retries <= MAX_RETRIES:
        result = execute_task(code)
        evaluation = evaluate_result(result)
        log_event(f"Execution result: {result}")
        log_event(f"Evaluation: {evaluation}")
        if result.get("error"):
            previous_fix = find_similar_fix(result["error"])
            if previous_fix:
                log_event("Using learned fix from memory")
                code = previous_fix
            else:
                suggestion = suggest_fix(goal, plan, code, result["error"])
                log_event(f"Self-debugging suggestion: {suggestion}")
                store_error_pattern(result["error"], suggestion)
                code = suggestion
            retries += 1
            if retries > MAX_RETRIES:
                break
            log_event(f"Retrying (attempt {retries})...")
        else:
            break
    store_result({
        "goal": goal,
        "plan": plan,
        "code": code,
        "result": result,
        "evaluation": evaluation
    })
    return evaluation
