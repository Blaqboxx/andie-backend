from openai import OpenAI
from agents.memory_agent import save_memory

client = OpenAI()


def extract_term(query: str):
    query = query.lower().strip()

    if " is " in query:
        term = query.split(" is ")[-1]
    else:
        term = query

    return term.replace("?", "").strip()


def ask_llm(query: str):
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
    except:
        pass

    return answer
