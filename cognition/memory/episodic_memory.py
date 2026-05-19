"""
STEP 11B — Episodic Memory
==========================
Stores structured operational experiences — every task execution, its outcome,
what recovery was used, the confidence level, and which agent/node handled it.

This is ANDIE's operational experience log: the raw history of what happened.

Episode schema
--------------
    task            — task name / description slug
    outcome         — "success" | "failure" | "partial_success" | "blocked"
    reason          — free-text cause (e.g. "GPU unavailable", "syntax_error")
    recovery_used   — recovery strategy name or None
    confidence      — epistemic confidence at time of evaluation (0-1)
    agent_id        — agent that executed (or None)
    node_id         — infra node that ran the task (or None)
    tags            — arbitrary labels for indexing
    timestamp       — ISO-8601 UTC
    episode_id      — uuid4 short key

High-level API
--------------
    em = EpisodicMemory(store)
    ep = em.record("deploy_api", "failure", reason="OOM", recovery_used="reduce_scope")
    recent    = em.recent(10)
    failures  = em.failures_for("deploy_api")
    rate      = em.success_rate("deploy_api")       # float 0-1
    profile   = em.agent_profile("executor-1")      # dict summary
    node_hist = em.node_history("gpu-node")          # list of episodes
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .memory_store import MemoryStore

_NS = "episodes"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    return "ep-" + str(uuid.uuid4())[:8]


class Episode:
    """Lightweight wrapper around a stored episode dict."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._d = data

    # Accessors
    @property
    def episode_id(self)    -> str:           return self._d["episode_id"]
    @property
    def task(self)          -> str:           return self._d["task"]
    @property
    def outcome(self)       -> str:           return self._d["outcome"]
    @property
    def reason(self)        -> str:           return self._d.get("reason", "")
    @property
    def recovery_used(self) -> Optional[str]: return self._d.get("recovery_used")
    @property
    def confidence(self)    -> float:         return self._d.get("confidence", 1.0)
    @property
    def agent_id(self)      -> Optional[str]: return self._d.get("agent_id")
    @property
    def node_id(self)       -> Optional[str]: return self._d.get("node_id")
    @property
    def tags(self)          -> List[str]:     return self._d.get("tags", [])
    @property
    def timestamp(self)     -> str:           return self._d["timestamp"]

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._d)

    def __repr__(self) -> str:
        return f"Episode({self.episode_id} task={self.task!r} outcome={self.outcome})"


class EpisodicMemory:
    """Persistent store of operational experiences.

    All episodes are persisted via MemoryStore and survive restarts.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    # ── Record ────────────────────────────────────────────────────────────────

    def record(
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
        """Persist a new episode and return it."""
        eid = _make_id()
        data: Dict[str, Any] = {
            "episode_id":    eid,
            "task":          task,
            "outcome":       outcome,
            "reason":        reason,
            "recovery_used": recovery_used,
            "confidence":    round(float(confidence), 4),
            "agent_id":      agent_id,
            "node_id":       node_id,
            "tags":          list(tags or []),
            "timestamp":     _now_iso(),
        }
        self._store.put(_NS, eid, data)
        return Episode(data)

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def _all(self) -> List[Episode]:
        return [Episode(d) for d in self._store.all(_NS)]

    def _sorted(self) -> List[Episode]:
        """All episodes sorted newest-first."""
        return sorted(self._all(), key=lambda e: e.timestamp, reverse=True)

    def recent(self, n: int = 20) -> List[Episode]:
        return self._sorted()[:n]

    def for_task(self, task: str) -> List[Episode]:
        """All episodes whose task name contains *task* (case-insensitive)."""
        tl = task.lower()
        return [e for e in self._sorted() if tl in e.task.lower()]

    def failures_for(self, task: str) -> List[Episode]:
        return [e for e in self.for_task(task) if e.outcome in ("failure", "epistemic_failure")]

    def successes_for(self, task: str) -> List[Episode]:
        return [e for e in self.for_task(task) if e.outcome == "success"]

    def success_rate(self, task: str) -> float:
        episodes = self.for_task(task)
        if not episodes:
            return 0.0
        return round(len([e for e in episodes if e.outcome == "success"]) / len(episodes), 3)

    def with_tag(self, tag: str) -> List[Episode]:
        return [e for e in self._sorted() if tag in e.tags]

    def with_outcome(self, outcome: str) -> List[Episode]:
        return [e for e in self._sorted() if e.outcome == outcome]

    def by_recovery(self, strategy: str) -> List[Episode]:
        """All episodes where this recovery strategy was used."""
        return [e for e in self._sorted() if e.recovery_used == strategy]

    # ── Profiling ─────────────────────────────────────────────────────────────

    def agent_profile(self, agent_id: str) -> Dict[str, Any]:
        """Aggregated statistics for a specific agent."""
        eps = [e for e in self._sorted() if e.agent_id == agent_id]
        if not eps:
            return {"agent_id": agent_id, "total": 0}
        successes = [e for e in eps if e.outcome == "success"]
        failures  = [e for e in eps if e.outcome in ("failure", "epistemic_failure")]
        avg_conf  = sum(e.confidence for e in eps) / len(eps)
        strategies = {}
        for e in eps:
            if e.recovery_used:
                strategies[e.recovery_used] = strategies.get(e.recovery_used, 0) + 1
        return {
            "agent_id":       agent_id,
            "total":          len(eps),
            "success":        len(successes),
            "failure":        len(failures),
            "success_rate":   round(len(successes) / len(eps), 3),
            "avg_confidence": round(avg_conf, 3),
            "top_recovery":   max(strategies, key=strategies.get) if strategies else None,
        }

    def node_history(self, node_id: str) -> List[Episode]:
        """All episodes executed on a specific infrastructure node."""
        return [e for e in self._sorted() if e.node_id == node_id]

    def node_reliability(self, node_id: str) -> float:
        """Success rate for a specific infrastructure node (0-1)."""
        eps = self.node_history(node_id)
        if not eps:
            return 0.0
        return round(len([e for e in eps if e.outcome == "success"]) / len(eps), 3)

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        all_eps = self._sorted()
        if not all_eps:
            return {"total": 0}
        outcomes: Dict[str, int] = {}
        for e in all_eps:
            outcomes[e.outcome] = outcomes.get(e.outcome, 0) + 1
        avg_conf = sum(e.confidence for e in all_eps) / len(all_eps)
        return {
            "total":          len(all_eps),
            "outcomes":       outcomes,
            "avg_confidence": round(avg_conf, 3),
            "most_recent":    all_eps[0].timestamp if all_eps else None,
        }

    def __len__(self) -> int:
        return self._store.count(_NS)
