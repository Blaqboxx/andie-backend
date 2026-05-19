"""
SemanticMemory: Full semantic knowledge layer for Andie
- Stores and retrieves structured knowledge triples (subject, relation, object)
- Supports graph-based queries and reasoning
- Integrates with vector memory for hybrid recall
"""
import time
from collections import defaultdict

class SemanticMemory:
    def __init__(self):
        self.triples = []  # (subject, relation, object, meta)
        self.index = defaultdict(list)  # subject -> [triple indices]

    def add(self, subject, relation, obj, meta=None):
        idx = len(self.triples)
        triple = (subject, relation, obj, meta or {"timestamp": time.time()})
        self.triples.append(triple)
        self.index[subject].append(idx)
        self.index[obj].append(idx)

    def query(self, subject=None, relation=None, obj=None):
        # Simple graph query: match any provided field
        results = []
        for i, (s, r, o, m) in enumerate(self.triples):
            if subject and s != subject:
                continue
            if relation and r != relation:
                continue
            if obj and o != obj:
                continue
            results.append((s, r, o, m))
        return results

    def related(self, node):
        # Return all triples where node is subject or object
        return [self.triples[i] for i in self.index.get(node, [])]

    def as_graph(self):
        # Return as adjacency list
        graph = defaultdict(list)
        for s, r, o, m in self.triples:
            graph[s].append((r, o, m))
        return dict(graph)
