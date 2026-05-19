"""
STEP 11C — Semantic Memory
==========================
Stores generalised, abstract lessons ANDIE has learned — patterns distilled
from episodic experience and consensus outcomes.

Unlike episodic memory (raw events), semantic memory stores *generalised*
operational intelligence: what tends to work, what patterns predict failure,
and what actions are recommended for known contexts.

SemanticFact schema
-------------------
    pattern             — unique label for the pattern/situation
    context_tags        — tags that indicate this pattern applies
    recommended_action  — the best-known action for this situation
    confidence          — how certain we are (0-1, reinforced over time)
    reinforcements      — how many times this fact has been confirmed
    source              — "reflection" | "consensus" | "recovery" | "manual"
    examples            — list of episode_ids that support this fact
    timestamp_first     — when first learned
    timestamp_last      — when last reinforced

High-level API
--------------
    sm = SemanticMemory(store)
    sm.learn("high_gpu_pressure", "reduce_parallelism", confidence=0.8, source="reflection")
    sm.learn("high_gpu_pressure", "reduce_parallelism", confidence=0.85)  # reinforces
    fact = sm.lookup("high_gpu_pressure")
    best = sm.best_action_for(["gpu_pressure", "high_load"])
    all_patterns = sm.all_patterns()
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .memory_store import MemoryStore

_NS = "semantic"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SemanticFact:
    """A generalised learned abstraction."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._d = data

    @property
    def pattern(self)             -> str:       return self._d["pattern"]
    @property
    def context_tags(self)        -> List[str]: return self._d.get("context_tags", [])
    @property
    def recommended_action(self)  -> str:       return self._d.get("recommended_action", "")
    @property
    def confidence(self)          -> float:     return self._d.get("confidence", 0.5)
    @property
    def reinforcements(self)      -> int:       return self._d.get("reinforcements", 1)
    @property
    def source(self)              -> str:       return self._d.get("source", "unknown")
    @property
    def examples(self)            -> List[str]: return self._d.get("examples", [])
    @property
    def timestamp_first(self)     -> str:       return self._d.get("timestamp_first", "")
    @property
    def timestamp_last(self)      -> str:       return self._d.get("timestamp_last", "")

    def matches_context(self, tags: List[str]) -> bool:
        """True when any context_tag of this fact appears in the given tags."""
        if not self.context_tags:
            return False
        return any(ct in tags for ct in self.context_tags)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._d)

    def __repr__(self) -> str:
        return (f"SemanticFact(pattern={self.pattern!r} "
                f"action={self.recommended_action!r} conf={self.confidence:.2f} "
                f"reinforced×{self.reinforcements})")


class SemanticMemory:
    """Persistent store of generalised operational intelligence.

    Calling ``learn()`` for an existing pattern *reinforces* it — the
    confidence is updated as a weighted average and the reinforcement count
    is incremented.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    # ── Learn / reinforce ─────────────────────────────────────────────────────

    def learn(
        self,
        pattern:            str,
        recommended_action: str,
        confidence:         float              = 0.7,
        source:             str                = "reflection",
        context_tags:       Optional[List[str]] = None,
        example_episode_id: Optional[str]       = None,
    ) -> SemanticFact:
        """Learn or reinforce a semantic pattern.

        If the pattern already exists, confidence is updated as a decaying
        average (new_conf = old_conf * 0.7 + new_conf * 0.3) and
        reinforcements are incremented.  Otherwise a new fact is created.
        """
        existing = self._store.get(_NS, pattern)
        if existing:
            old_conf         = existing["confidence"]
            new_conf         = round(old_conf * 0.7 + confidence * 0.3, 4)
            existing["confidence"]     = new_conf
            existing["reinforcements"] = existing.get("reinforcements", 1) + 1
            existing["timestamp_last"] = _now_iso()
            existing["recommended_action"] = recommended_action
            if example_episode_id and example_episode_id not in existing.get("examples", []):
                existing.setdefault("examples", []).append(example_episode_id)
            self._store.put(_NS, pattern, existing)
            return SemanticFact(existing)

        data: Dict[str, Any] = {
            "pattern":            pattern,
            "context_tags":       list(context_tags or []),
            "recommended_action": recommended_action,
            "confidence":         round(float(confidence), 4),
            "reinforcements":     1,
            "source":             source,
            "examples":           [example_episode_id] if example_episode_id else [],
            "timestamp_first":    _now_iso(),
            "timestamp_last":     _now_iso(),
        }
        self._store.put(_NS, pattern, data)
        return SemanticFact(data)

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def lookup(self, pattern: str) -> Optional[SemanticFact]:
        d = self._store.get(_NS, pattern)
        return SemanticFact(d) if d else None

    def all_patterns(self) -> List[SemanticFact]:
        return [SemanticFact(d) for d in self._store.all(_NS)]

    def best_action_for(self, context_tags: List[str]) -> Optional[SemanticFact]:
        """Return the highest-confidence fact whose context_tags overlap the given tags."""
        candidates = [
            f for f in self.all_patterns() if f.matches_context(context_tags)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda f: f.confidence)

    def top_patterns(self, n: int = 10) -> List[SemanticFact]:
        """Return the n most-reinforced patterns."""
        return sorted(self.all_patterns(), key=lambda f: f.reinforcements, reverse=True)[:n]

    def high_confidence(self, threshold: float = 0.75) -> List[SemanticFact]:
        return [f for f in self.all_patterns() if f.confidence >= threshold]

    # ── Forget ────────────────────────────────────────────────────────────────

    def forget(self, pattern: str) -> bool:
        """Remove a pattern (e.g. superseded by new evidence)."""
        return self._store.delete(_NS, pattern)

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        facts = self.all_patterns()
        if not facts:
            return {"total": 0}
        avg_conf = sum(f.confidence for f in facts) / len(facts)
        return {
            "total":          len(facts),
            "avg_confidence": round(avg_conf, 3),
            "top_patterns":   [f.pattern for f in self.top_patterns(5)],
        }

    def __len__(self) -> int:
        return self._store.count(_NS)
