from threading import Lock


class MemoryService:
    def __init__(self):
        self._init_lock = Lock()
        self.initialized = False
        self.memory = []
        self.initialize()

    def initialize(self):
        # Idempotent guard so repeated startup paths do not duplicate state.
        if self.initialized:
            return
        with self._init_lock:
            if self.initialized:
                return
            # Fallback in-memory store since vector_store is a placeholder.
            if not isinstance(self.memory, list):
                self.memory = []
            self.initialized = True

    def store_memory(self, content, metadata=None):
        self.initialize()
        entry = {"content": content, "metadata": metadata or {}}
        self.memory.append(entry)
        return {"status": "stored", "entry": entry}

    def query_memory(self, query, top_k=5):
        self.initialize()
        # Simple search: return last top_k entries containing the query string
        results = [m for m in self.memory if query.lower() in m["content"].lower()]
        return {"results": results[-top_k:]}
