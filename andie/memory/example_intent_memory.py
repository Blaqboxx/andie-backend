"""
Example: Intent-aware memory storage and retrieval
"""
from andie_backend.memory.memory_service import MemoryService
import time

# Initialize with disk persistence (optional)
mem = MemoryService(persist_dir="/tmp/andie_vector_memory")

# --- Store memories with different intents ---
user_inputs = [
    "How do I fix a bug in my PWA?",  # debugging
    "What is a service worker?",      # learning
    "Build a new API endpoint",       # building
    "Explain how cache versioning works", # learning
    "Implement authentication for the backend" # building
]

for i, user_input in enumerate(user_inputs):
    meta = {
        "confidence": 0.8,
        "timestamp": time.time(),
        "feedback": "positive" if i % 2 == 0 else "neutral"
    }
    mem.store_memory(
        content=f"User: {user_input}\nAndie: [response {i}]",
        metadata=meta,
        user_input=user_input
    )

# --- Query with intent filtering ---
query = "How do I debug errors in my app?"  # Should match debugging intent
results = mem.query_memory(query, top_k=3)
print("Relevant memories for query (intent-aware):")
for r in results["results"]:
    print(f"- {r['text']} (intent: {r['meta'].get('intent')})")

# Try a learning query
query2 = "Explain service workers"
results2 = mem.query_memory(query2, top_k=3)
print("\nRelevant memories for learning query:")
for r in results2["results"]:
    print(f"- {r['text']} (intent: {r['meta'].get('intent')})")
