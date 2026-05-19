"""
Test script for semantic KV response cache in LLM router
- Sends repeated and paraphrased queries
- Measures response time and cache hits
"""
import time
from .llm_router import call_llm

# Test queries (paraphrased)
queries = [
    "How do I fix a bug in my PWA?",
    "What's the best way to debug a PWA?",
    "How can I troubleshoot issues in my progressive web app?",
    "How do I fix a bug in my PWA?"  # exact repeat
]

context = "User is working on a Vite PWA with service worker issues."

for i, q in enumerate(queries):
    start = time.time()
    response = call_llm(q, context=context, cache=True)
    elapsed = time.time() - start
    print(f"\nQuery {i+1}: {q}")
    print(f"Response: {response[:120]}...")
    print(f"Time: {elapsed:.2f}s (cache {'HIT' if elapsed < 1.0 else 'MISS'})")
