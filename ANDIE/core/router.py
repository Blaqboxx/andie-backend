from agents.memory_agent import get_memory
from agents.encyclopedia_agent import search_encyclopedia
from agents.llm_agent import ask_llm
from core.intent import classify_intent


def extract_term(query: str):
    query = query.lower().strip()

    if " is " in query:
        term = query.split(" is ")[-1]
    else:
        term = query

    return term.replace("?", "").strip()


def route_query(query: str):
    intent = classify_intent(query)

    print(f"[ROUTER] Intent: {intent}")
    print(f"[ROUTER] Query: {query}")

    term = extract_term(query)
    print(f"[ROUTER] Term: {term}")

    # 🧠 1. MEMORY AGENT (FIRST ALWAYS)
    memory = get_memory(term)
    if memory:
        print("[AGENT] Memory hit")
        return memory

    # 🌐 2. KNOWLEDGE AGENT
    if intent == "knowledge":
        result = search_encyclopedia(query)
        if result:
            print("[AGENT] Knowledge hit")
            return result

    # 🤖 3. LLM AGENT (LAST RESORT)
    print("[AGENT] LLM fallback")
    return ask_llm(query)
