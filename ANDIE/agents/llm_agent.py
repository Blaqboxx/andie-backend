
from openai import OpenAI
from agents.memory_agent import save_memory
from valhalla.controller.sandbox_manager import run_in_sandbox

client = OpenAI()


def extract_term(query: str):
    query = query.lower().strip()

    if " is " in query:
        term = query.split(" is ")[-1]
    else:
        term = query

    return term.replace("?", "").strip()


def ask_llm(query: str):
    # Detect if the query is code (simple heuristic: contains 'def', 'import', or ends with ':')
    is_code = any(keyword in query for keyword in ["def ", "import ", "class ", "lambda ", "print("]) or query.strip().endswith(":")

    if is_code:
        # Send code to VALHALLA for execution
        result = run_in_sandbox(query)
        answer = str(result)
    else:
        response = client.responses.create(
            model="gpt-4o-mini",
            input=query
        )
        answer = response.output[0].content[0].text

    # 🔥 SELF-LEARNING
    try:
        term = extract_term(query)
        if len(term) < 50:  # prevent saving garbage
            save_memory(term, answer)
            print(f"[MEMORY] Learned: {term}")
    except Exception as e:
        print(f"[MEMORY] Save failed: {e}")

    return answer
