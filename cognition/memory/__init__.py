"""
cognition.memory — STEP 11 Persistent Cognitive Memory
=======================================================
Long-term operational intelligence layer for ANDIE.

Modules
-------
memory_store      — JSON-backed persistent namespaced KV store
episodic_memory   — Structured experience episodes (task outcomes, recoveries)
semantic_memory   — Generalised learned patterns and recommendations
experience_index  — Cross-memory queries (familiarity, reliability, consensus)
memory_retriever  — High-level unified API ("Have I seen this before?")

Quick start
-----------
    from cognition.memory import MemoryRetriever

    # Full persistent stack from a directory:
    memory = MemoryRetriever.from_dir("/var/andie/memory")

    # Or ephemeral (for tests):
    memory = MemoryRetriever.ephemeral()

    # Before planning:
    recall = memory.recall("deploy_api", context_tags=["gpu_pressure"])

    # After execution:
    memory.record_outcome("deploy_api", "failure", reason="OOM",
                          recovery_used="reduce_scope", confidence=0.44)

    # Learn from experience:
    memory.learn("oom_during_deploy", "reduce_scope",
                 context_tags=["deploy", "oom"], confidence=0.82)

    # Suggest recovery for future failure:
    strategy = memory.suggest_recovery("deploy_api", "OOM")
"""

from .episodic_memory  import Episode, EpisodicMemory
from .experience_index import ExperienceIndex
from .memory_retriever import MemoryRetriever, RecallResult
from .memory_store     import MemoryStore
from .semantic_memory  import SemanticFact, SemanticMemory

__all__ = [
    # store
    "MemoryStore",
    # episodic
    "Episode",
    "EpisodicMemory",
    # semantic
    "SemanticFact",
    "SemanticMemory",
    # index
    "ExperienceIndex",
    # retriever (primary API)
    "MemoryRetriever",
    "RecallResult",
]
