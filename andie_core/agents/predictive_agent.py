from andie_core.brain.llm_router import call_llm
import os

def run(logs: str = ""):
    print("[PredictiveAgent] Analyzing logs for patterns...")
    # Example: Use LLM to predict issues
    prompt = f"Analyze the following logs and predict possible failures.\nLogs:\n{logs}"
    result = call_llm(prompt=prompt, system="You are a predictive maintenance agent.")
    print(f"[PredictiveAgent] LLM prediction: {result}")
    return result
