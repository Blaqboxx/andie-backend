"""
STEP 11D — Experience Index
============================
Cross-memory fast lookup index.  Answers high-level questions by querying
episodic + semantic memory together.

Questions it answers
--------------------
- Have I seen this task before?            seen_before(task)
- What is the node reliability?            node_reliability(node_id)
- What do I know about this agent?         agent_profile(agent_id)
- What patterns match this context?        known_patterns(context_tags)
- What was the consensus outcome for X?    consensus_history(topic)
- What recovery strategy worked for X?     best_recovery_for(task, failure_reason)

Consensus history
-----------------
The index stores consensus outcomes (approved/rejected, ratio, topic) as a
separate namespace in MemoryStore so governance history survives restarts.
Use ``record_consensus(result_dict)`` to persist each ConsensusResult.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .episodic_memory import Episode, EpisodicMemory
from .memory_store    import MemoryStore
from .semantic_memory import SemanticFact, SemanticMemory

_NS_CONSENSUS = "consensus_history"
_NS_ROUTING   = "routing_history"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperienceIndex:
    """Cross-memory index providing high-level operational intelligence queries."""

    def __init__(
        self,
        store:    MemoryStore,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
    ) -> None:
        self._store    = store
        self._episodic = episodic
        self._semantic = semantic

    # ── Task familiarity ──────────────────────────────────────────────────────

    def seen_before(self, task: str) -> Dict[str, Any]:
        """Return whether ANDIE has seen this task before and key statistics."""
        episodes = self._episodic.for_task(task)
        if not episodes:
            return {"seen": False, "task": task}
        most_recent = episodes[0]
        return {
            "seen":        True,
            "task":        task,
            "total":       len(episodes),
            "success_rate": self._episodic.success_rate(task),
            "most_recent": {
                "outcome":       most_recent.outcome,
                "reason":        most_recent.reason,
                "recovery_used": most_recent.recovery_used,
                "confidence":    most_recent.confidence,
                "timestamp":     most_recent.timestamp,
            },
        }

    # ── Recovery intelligence ─────────────────────────────────────────────────

    def best_recovery_for(self, task: str, failure_reason: str = "") -> Optional[str]:
        """Return the recovery strategy that most often led to success for this task."""
        # 1. Check semantic memory for a direct pattern match
        tags = [task.lower().replace(" ", "_")]
        if failure_reason:
            tags.append(failure_reason.lower().replace(" ", "_")[:30])
        fact = self._semantic.best_action_for(tags)
        if fact and fact.confidence >= 0.6:
            return fact.recommended_action

        # 2. Fall back to episodic: which recovery was used in successful episodes?
        successes = self._episodic.successes_for(task)
        strategies: Dict[str, int] = {}
        for ep in successes:
            if ep.recovery_used:
                strategies[ep.recovery_used] = strategies.get(ep.recovery_used, 0) + 1
        if strategies:
            return max(strategies, key=strategies.get)
        return None

    # ── Agent / node intelligence ─────────────────────────────────────────────

    def agent_profile(self, agent_id: str) -> Dict[str, Any]:
        return self._episodic.agent_profile(agent_id)

    def node_reliability(self, node_id: str) -> float:
        return self._episodic.node_reliability(node_id)

    def infrastructure_summary(self) -> List[Dict[str, Any]]:
        """Reliability summary for every node seen in episodic memory."""
        node_ids: set = set()
        for ep in self._episodic.recent(500):
            if ep.node_id:
                node_ids.add(ep.node_id)
        return [
            {
                "node_id":     nid,
                "reliability": self.node_reliability(nid),
                "episodes":    len(self._episodic.node_history(nid)),
            }
            for nid in sorted(node_ids)
        ]

    # ── Semantic pattern lookup ───────────────────────────────────────────────

    def known_patterns(self, context_tags: List[str]) -> List[SemanticFact]:
        """Return all semantic facts that match the given context tags."""
        return [f for f in self._semantic.all_patterns() if f.matches_context(context_tags)]

    # ── Consensus history ─────────────────────────────────────────────────────

    def record_consensus(self, result: Dict[str, Any]) -> None:
        """Persist a consensus result for long-term governance memory."""
        key = f"consensus-{result.get('request_id', _now_iso())}"
        self._store.put(_NS_CONSENSUS, key, {**result, "indexed_at": _now_iso()})

    def consensus_history(self, topic: str = "", n: int = 50) -> List[Dict[str, Any]]:
        """Return past consensus results, optionally filtered by topic substring."""
        all_results = self._store.all(_NS_CONSENSUS)
        if topic:
            tl = topic.lower()
            all_results = [r for r in all_results if tl in r.get("topic", "").lower()]
        return sorted(all_results, key=lambda r: r.get("indexed_at", ""), reverse=True)[:n]

    def consensus_approval_rate(self, topic: str = "") -> float:
        """Overall approval rate across all matching consensus decisions."""
        history = self.consensus_history(topic, n=1000)
        if not history:
            return 0.0
        approved = [r for r in history if r.get("approved")]
        return round(len(approved) / len(history), 3)

    # ── Routing history ───────────────────────────────────────────────────────

    def record_routing(self, task_id: str, agent_id: str, node_id: Optional[str],
                       outcome: str) -> None:
        """Log a routing decision outcome for future routing intelligence."""
        key = f"route-{task_id}-{_now_iso()}"
        self._store.put(_NS_ROUTING, key, {
            "task_id":    task_id,
            "agent_id":   agent_id,
            "node_id":    node_id,
            "outcome":    outcome,
            "timestamp":  _now_iso(),
        })

    def routing_success_rate(self, agent_id: str) -> float:
        """Fraction of tasks routed to this agent that succeeded."""
        routes = self._store.query(_NS_ROUTING, lambda r: r.get("agent_id") == agent_id)
        if not routes:
            return 0.0
        success = [r for r in routes if r.get("outcome") == "success"]
        return round(len(success) / len(routes), 3)

    # ── Full diagnostic snapshot ──────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        return {
            "episodic":   self._episodic.summary(),
            "semantic":   self._semantic.summary(),
            "store_stats": self._store.stats(),
        }
