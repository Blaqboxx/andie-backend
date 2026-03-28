def classify_intent(query: str):
    query = query.lower()

    if query.startswith(("what is", "define", "meaning of")):
        return "definition"

    if len(query.split()) > 5:
        return "knowledge"

    return "llm"
