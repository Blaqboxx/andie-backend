class MemoryService:
    def __init__(self):
        # Fallback in-memory store since vector_store is a placeholder
        self.memory = []

    def store_memory(self, content, metadata=None):
        entry = {"content": content, "metadata": metadata or {}}
        self.memory.append(entry)
        return {"status": "stored", "entry": entry}

    def query_memory(self, query, top_k=5):
        # Simple search: return last top_k entries containing the query string
        results = [m for m in self.memory if query.lower() in m["content"].lower()]
        return {"results": results[-top_k:]}
