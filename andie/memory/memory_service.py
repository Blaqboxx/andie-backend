def classify_intent(text):
    text = text.lower()
    if any(k in text for k in ["bug", "error", "not working", "fix"]):
        return "debugging"
    if any(k in text for k in ["how", "explain", "what is"]):
        return "learning"
    if any(k in text for k in ["build", "create", "implement"]):
        return "building"
    return "general"

from .vector_store import VectorStore
import os
import time

class MemoryService:
    def retrieve(self, observations, k=5, tags=None):
        """
        Retrieve relevant memories for a list of observations, using hybrid search and scoring.
        Returns a list of memory dicts (content + metadata), sorted by weight.
        """
        if not observations:
            return []
        # Aggregate results for all observations
        results = []
        for obs in observations:
            query = obs if isinstance(obs, str) else str(obs)
            search = self.hybrid_search(query, k=k, tags=tags)
            for r in search["results"]:
                # Only include content + metadata
                results.append({
                    "content": r.get("text"),
                    "metadata": r.get("meta"),
                    "weight": r.get("weight", 0)
                })
        # Sort by weight descending
        results.sort(key=lambda x: x["weight"], reverse=True)
        return results[:k]

    def __init__(self, persist_dir=None):
        # Multi-tier: STM and LTM
        persist_stm = None
        persist_ltm = None
        if persist_dir:
            os.makedirs(persist_dir, exist_ok=True)
            persist_stm = os.path.join(persist_dir, "stm_vector_memory")
            persist_ltm = os.path.join(persist_dir, "ltm_vector_memory")
        try:
            self.stm = VectorStore(persist_path=persist_stm)
            self.ltm = VectorStore(persist_path=persist_ltm)
            self.available = True
        except Exception as e:
            print(f"[MemoryService] VectorStore unavailable: {e}")
            self.stm = None
            self.ltm = None
            self.available = False
        self.memory = []  # fallback in-memory


    def store_memory(self, content, metadata=None, user_input=None):
        # Serialize dict content to string for intent classification and storage
        import json
        if isinstance(content, dict):
            content_str = json.dumps(content, default=str)
        else:
            content_str = content
        # Intent tagging
        intent = classify_intent(user_input or content_str)
        meta = dict(metadata or {})
        meta["intent"] = intent
        meta.setdefault("tags", []).append(intent)
        if not self.available:
            entry = {"content": content, "metadata": meta}
            self.memory.append(entry)
            return {"status": "stored", "entry": entry}

        # Add to STM always
        self.stm.add(content_str, meta=meta)
        # Promote to LTM if strong (positive feedback or high confidence)
        if meta.get("feedback") == "positive" or meta.get("confidence", 0) > 0.8:
            self.ltm.add(content_str, meta=meta)
        return {"status": "stored", "entry": {"content": content_str, "metadata": meta}}

    def hybrid_search(self, query, k=5, tags=None):
        # Intent-based filtering
        intent = classify_intent(query)
        if not self.available:
            results = [m for m in self.memory if query.lower() in m["content"].lower() and m["metadata"].get("intent") == intent]
            return {"results": results[-k:]}

        # Search STM and LTM, filter by tags and intent
        filter_tags = (tags or []) + [intent]
        stm_results = self.stm.search(query, k=k*2, filter_tags=filter_tags, return_scores=True)
        ltm_results = self.ltm.search(query, k=k*2, filter_tags=filter_tags, return_scores=True)
        all_results = stm_results + ltm_results
        # Score and sort
        for r in all_results:
            r["weight"] = self.stm.compute_weight(r["meta"], r["similarity"])
        all_results.sort(key=lambda r: r["weight"], reverse=True)
        return {"results": all_results[:k]}

    def query_memory(self, query, top_k=5, tags=None):
        # Default: hybrid search with scoring
        return self.hybrid_search(query, k=top_k, tags=tags)

    def reinforce_memory(self, idx, tier="stm", positive=True, confidence=None):
        if not self.available:
            return
        store = self.stm if tier == "stm" else self.ltm
        store.reinforce(idx, positive=positive, confidence=confidence)
