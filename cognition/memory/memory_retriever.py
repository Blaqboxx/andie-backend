"""
STEP 11E — Memory Retriever
============================
High-level facade over the entire STEP 11 memory layer.

This is the single entry-point that the rest of the cognition stack calls
before planning, routing, and execution.  It answers:

    "Have I seen this before?"
    "What should I do when X fails?"
    "Which node should I trust for this?"
    "What has consensus decided about this topic?"

It also provides convenient factory methods to construct the full memory
stack from a single directory path.

Usage
-----
    retriever = MemoryRetriever.from_dir("/var/andie/memory")

    # Before planning:
    recall = retriever.recall("deploy_api", ["gpu_pressure"])
    if recall["seen_before"]["seen"]:
        print(recall["seen_before"]["success_rate"])

    # After execution:
    retriever.record_outcome(
        task="deploy_api", outcome="failure",
        reason="OOM", recovery_used="reduce_scope",
        confidence=0.44, agent_id="executor-1", node_id="nuc-main",
    )

    # Before retry:
    strategy = retriever.suggest_recovery("deploy_api", "OOM")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .episodic_memory  import Episode, EpisodicMemory
from .experience_index import ExperienceIndex
from .memory_store     import MemoryStore
from .semantic_memory  import SemanticFact, SemanticMemory


class RecallResult:
    """Result of a memory recall query."""

    def __init__(
        self,
        task:         str,
        seen_before:  Dict[str, Any],
        recent_eps:   List[Episode],
        patterns:     List[SemanticFact],
        suggestion:   Optional[str],
    ) -> None:
        self.task        = task
        self.seen_before = seen_before
        self.recent_eps  = recent_eps
        self.patterns    = patterns
        self.suggestion  = suggestion          # recommended recovery or action

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task":        self.task,
            "seen_before": self.seen_before,
            "recent_episodes": [e.to_dict() for e in self.recent_eps[:5]],
            "matching_patterns": [p.to_dict() for p in self.patterns],
            "suggestion":  self.suggestion,
        }

    def __repr__(self) -> str:
        seen = self.seen_before.get("seen", False)
        return (f"RecallResult(task={self.task!r} seen={seen} "
                f"episodes={len(self.recent_eps)} patterns={len(self.patterns)} "
                f"suggestion={self.suggestion!r})")


class MemoryRetriever:
    """Unified memory API for the ANDIE cognition runtime.

    Composes MemoryStore → EpisodicMemory + SemanticMemory → ExperienceIndex
    into a single coherent interface.
    """

    def __init__(
        self,
        store:    MemoryStore,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        index:    ExperienceIndex,
    ) -> None:
        self._store    = store
        self._episodic = episodic
        self._semantic = semantic
        self._index    = index

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_dir(cls, directory: str | Path, auto_flush: bool = True) -> "MemoryRetriever":
        """Construct the full memory stack from a directory path."""
        path    = Path(directory) / "andie_memory.json"
        store   = MemoryStore(path, auto_flush=auto_flush)
        episodic = EpisodicMemory(store)
        semantic = SemanticMemory(store)
        index    = ExperienceIndex(store, episodic, semantic)
        return cls(store, episodic, semantic, index)

    @classmethod
    def ephemeral(cls) -> "MemoryRetriever":
        """In-memory only (for tests). Data is not persisted to disk."""
        import tempfile
        tmp = tempfile.mkdtemp()
        return cls.from_dir(tmp, auto_flush=False)

    # ── Core recall ───────────────────────────────────────────────────────────

    def recall(
        self,
        task:         str,
        context_tags: Optional[List[str]] = None,
        n_episodes:   int = 5,
    ) -> RecallResult:
        """Ask: 'Have I seen this before? What do I know?'"""
        tags         = list(context_tags or [])
        seen         = self._index.seen_before(task)
        recent_eps   = self._episodic.for_task(task)[:n_episodes]
        patterns     = self._index.known_patterns(tags)
        suggestion   = self._index.best_recovery_for(task)
        return RecallResult(task, seen, recent_eps, patterns, suggestion)

    # ── Record outcomes ───────────────────────────────────────────────────────

    def record_outcome(
        self,
        task:          str,
        outcome:       str,
        reason:        str                = "",
        recovery_used: Optional[str]      = None,
        confidence:    float              = 1.0,
        agent_id:      Optional[str]      = None,
        node_id:       Optional[str]      = None,
        tags:          Optional[List[str]] = None,
    ) -> Episode:
        """Persist a task execution outcome to episodic memory."""
        return self._episodic.record(
            task=task, outcome=outcome, reason=reason,
            recovery_used=recovery_used, confidence=confidence,
            agent_id=agent_id, node_id=node_id, tags=tags,
        )

    # ── Learn patterns ────────────────────────────────────────────────────────

    def learn(
        self,
        pattern:            str,
        recommended_action: str,
        confidence:         float              = 0.7,
        source:             str                = "reflection",
        context_tags:       Optional[List[str]] = None,
        example_episode_id: Optional[str]       = None,
    ) -> SemanticFact:
        """Teach ANDIE a generalised operational pattern."""
        return self._semantic.learn(
            pattern=pattern, recommended_action=recommended_action,
            confidence=confidence, source=source, context_tags=context_tags,
            example_episode_id=example_episode_id,
        )

    # ── Recovery suggestion ───────────────────────────────────────────────────

    def suggest_recovery(self, task: str, failure_reason: str = "") -> Optional[str]:
        """Return the best-known recovery strategy for a failing task."""
        return self._index.best_recovery_for(task, failure_reason)

    # ── Governance ────────────────────────────────────────────────────────────

    def record_consensus(self, result: Dict[str, Any]) -> None:
        self._index.record_consensus(result)

    def consensus_history(self, topic: str = "", n: int = 50) -> List[Dict[str, Any]]:
        return self._index.consensus_history(topic, n)

    def consensus_approval_rate(self, topic: str = "") -> float:
        return self._index.consensus_approval_rate(topic)

    # ── Routing ───────────────────────────────────────────────────────────────

    def record_routing(self, task_id: str, agent_id: str,
                       node_id: Optional[str], outcome: str) -> None:
        self._index.record_routing(task_id, agent_id, node_id, outcome)

    def routing_success_rate(self, agent_id: str) -> float:
        return self._index.routing_success_rate(agent_id)

    # ── Infrastructure ────────────────────────────────────────────────────────

    def node_reliability(self, node_id: str) -> float:
        return self._index.node_reliability(node_id)

    def infrastructure_summary(self) -> List[Dict[str, Any]]:
        return self._index.infrastructure_summary()

    def agent_profile(self, agent_id: str) -> Dict[str, Any]:
        return self._index.agent_profile(agent_id)

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        return self._index.snapshot()

    def flush(self) -> None:
        """Force write to disk (useful when auto_flush=False)."""
        self._store.flush()

    # ── Direct access (escape hatches) ───────────────────────────────────────

    @property
    def episodic(self) -> EpisodicMemory:
        return self._episodic

    @property
    def semantic(self) -> SemanticMemory:
        return self._semantic

    @property
    def index(self) -> ExperienceIndex:
        return self._index
