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


def llm_invoke(task, context=None, preferred=None):
    provider, model = route_model(task, context, preferred)
    if provider == "openai":
        response = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are ANDIE, an intelligent execution agent.\n\nAvailable tools:\n- run_command(command)\n- open_browser(url)\n\nYou MUST respond ONLY in JSON format:\n{\n  'action': '...',\n  'input': '...'}\n"},
                {"role": "user", "content": f"Task: {task}\nContext: {context}"}
            ],
            max_tokens=200
        )
        if hasattr(response, "choices") and response.choices:
            return response.choices[0].message.content
        return str(response)
    elif provider == "anthropic" and ANTHROPIC_AVAILABLE:
        # Anthropic Claude API example (pseudo, adjust as needed)
        completion = anthropic_client.messages.create(
            model=model,
            max_tokens=200,
            messages=[
                {"role": "user", "content": f"Task: {task}\nContext: {context}\nRespond ONLY in JSON: {{'action': '...', 'input': '...'}}"}
            ]
        )
        return completion.content[0].text if hasattr(completion, "content") else str(completion)
    else:
        return "[ERROR] No valid LLM provider available."
