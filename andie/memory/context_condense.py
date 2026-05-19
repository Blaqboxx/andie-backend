"""
Context condensation utility for Andie
- Summarizes a list of memory texts into a concise context block
- Uses LLM for summarization if available, else falls back to simple extractive summary
"""
from andie_backend.brain.llm_router import call_llm

def summarize_memories(memories, max_tokens=200):
    if not memories:
        return ""
    # Concatenate memory texts
    texts = [m["text"] if "text" in m else m.get("content", "") for m in memories]
    joined = "\n".join(texts)
    # Try LLM summarization
    try:
        prompt = f"Summarize the following past experiences for context (max {max_tokens} tokens):\n{joined}"
        summary = call_llm(prompt, model="gpt-4o")
        return summary.strip()
    except Exception:
        # Fallback: extract first N lines
        return "\n".join(texts[:5])
