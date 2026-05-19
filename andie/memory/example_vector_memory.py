# Example: Using MemoryService with VectorStore (semantic recall)

from andie_backend.memory.memory_service import MemoryService

# 1. Initialize with disk persistence (recommended for production)
mem = MemoryService(persist_dir="/tmp/andie_vector_memory")

# 2. Store a memory (embedding will be created and indexed)
mem.store_memory(
    content="User: How do I fix my PWA?\nAndie: Try updating your cache version.",
    metadata={"id": "ep1", "timestamp": "2026-04-18"}
)

# 3. Query for similar memories (semantic search)
results = mem.query_memory("Why is my PWA not updating?", top_k=3)
print("Relevant past interactions:")
for r in results["results"]:
    print("-", r["text"] if "text" in r else r["content"])

# Output will show semantically similar past interactions, not just string matches.
