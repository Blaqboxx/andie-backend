import os

# ===== Local LLM =====
def run_local_llm(prompt: str) -> str:
    try:
        from transformers import pipeline

        pipe = pipeline(
            "text-generation",
            model="distilgpt2",  # lightweight starter
            device="cpu"
        )

        result = pipe(prompt, max_length=100, num_return_sequences=1)
        return result[0]["generated_text"]

    except Exception as e:
        print(f"[LOCAL LLM ERROR] {e}")
        raise


# ===== OpenAI Fallback =====
def run_openai_llm(prompt: str) -> str:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )

        return response.choices[0].message.content

    except Exception as e:
        print(f"[OPENAI ERROR] {e}")
        raise


# ===== Hybrid Controller =====
def run_llm(prompt: str) -> str:
    # 1. Try LOCAL
    try:
        print("🧠 Trying LOCAL LLM...")
        return run_local_llm(prompt)
    except:
        pass

    # 2. Try OpenAI
    try:
        print("☁️ Falling back to OpenAI...")
        return run_openai_llm(prompt)
    except:
        pass

    # 3. Safe fallback
    print("⚠️ Using safe fallback")
    return f"[LLM unavailable] Echo: {prompt}"
