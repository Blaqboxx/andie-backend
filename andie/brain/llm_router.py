# --- Semantic Response Cache ---
import hashlib
VectorStore = None

# In-memory cache: key -> (response, embedding)
_response_cache = {}
# VectorStore for semantic cache keys (optional, for paraphrase matching)
_cache_vector_store = VectorStore(model_name='all-MiniLM-L6-v2')

def _get_cache_key(prompt, context=None, system=None):
    # Hash of prompt + context + system for exact match
    key_str = (system or "") + "||" + (context or "") + "||" + prompt
    return hashlib.sha256(key_str.encode('utf-8')).hexdigest()
import os
from openai import OpenAI
# Placeholder for Anthropic or other clients
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

# --- Initialize clients ---
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
if ANTHROPIC_AVAILABLE:
    anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
else:
    anthropic_client = None

# --- Model registry ---
MODELS = {
    "openai": {
        "client": openai_client,
        "models": ["gpt-4o", "gpt-4.1", "gpt-3.5-turbo"]
    },
    "anthropic": {
        "client": anthropic_client,
        "models": ["claude-3-opus-20240229", "claude-3-sonnet-20240229"] if ANTHROPIC_AVAILABLE else []
    }
}

# --- Routing logic ---
def route_model(task, context=None, preferred=None):
    """
    Route to the best LLM for the task.
    preferred: 'openai', 'anthropic', or None (auto)
    """
    # Example: route by keyword or explicit preference
    # if preferred == "anthropic" and ANTHROPIC_AVAILABLE:
    #     return "anthropic", "claude-3-sonnet-20240229"
    # if preferred == "openai" or not ANTHROPIC_AVAILABLE:
    #     return "openai", "gpt-4o"
    # Simple heuristic: long context -> Anthropic
    # if context and len(str(context)) > 4000 and ANTHROPIC_AVAILABLE:
    #     return "anthropic", "claude-3-sonnet-20240229"
    # return "openai", "gpt-4o"



# --- Unified LLM Gateway ---
def call_llm(prompt, system=None, context=None, model="gpt-4o", cache=True, similarity_threshold=0.95):
    if not prompt:
        raise ValueError("LLM requires a prompt")
    key = _get_cache_key(prompt, context, system)
    # 1. Exact match cache
    if cache and key in _response_cache:
        return _response_cache[key][0]
    # 2. Semantic cache (paraphrase match)
    if cache and len(_cache_vector_store.texts) > 0:
        query = prompt + "\n" + (context or "")
        results = _cache_vector_store.search(query, k=1)
        if results and results[0]["similarity"] > similarity_threshold:
            idx = _cache_vector_store.texts.index(results[0]["text"])
            cache_key = list(_response_cache.keys())[idx]
            return _response_cache[cache_key][0]
    # 3. Generate new response
    # --- Persona compliance: inject identity into both system and user ---
    persona = system or "You are ANDIE, an autonomous agent system. Never say you are ChatGPT. Never mention training data or knowledge cutoff. Never say you can't access external data."
    user_message = f"""
SYSTEM IDENTITY:\nYou are ANDIE, an autonomous AI system with real-time capabilities.\n\nUSER MESSAGE:\n{prompt}
"""
    messages = [
        {"role": "system", "content": persona},
        {"role": "user", "content": user_message}
    ]
    if context:
        messages.append({"role": "user", "content": f"Context:\n{context}"})
    print("\n🚨 FINAL MESSAGES SENT TO LLM:")
    for m in messages:
        print(m)
    response = openai_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.7
    )
    result = response.choices[0].message.content
    # 4. Store in cache (only if not too short/noisy)
    if cache and len(result.strip()) > 20:
        _response_cache[key] = (result, None)  # Embedding not needed for now
        _cache_vector_store.add(prompt + "\n" + (context or ""))
    return result
