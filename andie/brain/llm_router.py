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
def call_llm(prompt, system=None, context=None, model="gpt-4o"):
    if not prompt:
        raise ValueError("LLM requires a prompt")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    if context:
        messages.append({"role": "user", "content": f"Context:\n{context}"})
    messages.append({"role": "user", "content": prompt})
    response = openai_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.7
    )
    return response.choices[0].message.content
