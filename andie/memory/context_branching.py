"""
Context branching utility for Andie
- Generates multiple candidate responses for a given input/context
- Ranks and selects the best response
"""
from andie_backend.brain.llm_router import call_llm

def generate_branches(user_input, context, n=3, system=None):
    candidates = []
    for i in range(n):
        prompt = f"""
Context:
{context}

User:
{user_input}

Respond helpfully and concisely. Variant {i+1}.
"""
        response = call_llm(prompt, system=system)
        candidates.append(response.strip())
    return candidates

def rank_responses(responses, user_input, context, system=None):
    # Use LLM to rank responses by helpfulness
    prompt = f"Rank the following responses to the user input for helpfulness and relevance. Return the best one only.\nUser: {user_input}\nContext: {context}\nResponses:\n" + "\n---\n".join(responses)
    best = call_llm(prompt, system=system)
    return best.strip()
