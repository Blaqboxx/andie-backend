"""
STEP 12C — Forecast Engine
===========================
Predicts infrastructure pressure on a target node BEFORE a task is dispatched.

Signals used
------------
    1. Historical reliability   — long-run success rate from episodic memory
    2. Recent failure rate       — success rate of the last ``_RECENT_WINDOW``
                                   episodes on the node (recency-weighted)
    3. Semantic pattern risk     — any known patterns matching the context tags
                                   that are associated with node/infrastructure
                                   failure modes

The three signals are combined into a single ``failure_probability``, and the
engine emits an ``InfrastructureForecast`` with a ``PressureLevel``, contributing
pattern names, and recommended pre-execution actions.

Recency weighting
-----------------
Recent episodes are weighted more heavily than historical ones:

    failure_probability(recent) contributes at 2× the weight of historical.

This means a node that was reliable long-term but has had a bad last 5 runs will
still register elevated risk.

Usage
-----
    from cognition.memory import MemoryRetriever
    from cognition.prediction import ForecastEngine

    retriever = MemoryRetriever.from_dir("/var/andie/memory")
    engine    = ForecastEngine(retriever)

    forecast = engine.forecast(
        task="deploy_api",
        node_id="nuc-main",
        context_tags=["gpu_pressure", "deploy"],
    )
    print(forecast.pressure_level, forecast.failure_probability)
"""

from __future__ import annotations

from typing import List, Optional

from cognition.memory import MemoryRetriever
from .prediction_models import InfrastructureForecast, PressureLevel

# How many recent episodes to examine for the "recent window" signal
_RECENT_WINDOW = 5

# Weight of recent signal relative to historical (2x heavier)
_W_HISTORICAL = 1.0
_W_RECENT     = 2.0
_W_SEMANTIC   = 0.8

# Minimum episodes on a node before historical signal is trusted
_MIN_NODE_EPS = 2


def _weighted_failure_prob(pairs: list) -> tuple:
    """(probability, weight, confidence) → (combined_p, combined_c)."""
    total_w = sum(w * c for _, w, c in pairs if w > 0)
    if total_w == 0.0:
        return 0.0, 0.0
    p = sum(prob * w * c for prob, w, c in pairs if w > 0) / total_w
    c = sum(w * c for _, w, c in pairs if w > 0) / sum(w for _, w, _ in pairs if w > 0)
    return round(min(p, 1.0), 4), round(min(c, 1.0), 4)


class ForecastEngine:
    """Predicts infrastructure failure probability for a node before task dispatch.

    Parameters
    ----------
    retriever:
        Unified memory interface.
    recent_window:
        Number of most-recent episodes on a node to use for the recency signal.
    """

    def __init__(
        self,
        retriever:     MemoryRetriever,
        recent_window: int = _RECENT_WINDOW,
    ) -> None:
        self._mem    = retriever
        self._window = recent_window

    # ── Public API ────────────────────────────────────────────────────────────

    def forecast(
        self,
        task:         str,
        node_id:      str,
        context_tags: Optional[List[str]] = None,
    ) -> InfrastructureForecast:
        """Return an InfrastructureForecast for *node_id* before executing *task*."""
        tags = list(context_tags or [])

        # ── Signal 1: Historical reliability ─────────────────────────────────
        hist_p, hist_c = self._historical_signal(node_id)

        # ── Signal 2: Recent failure rate ─────────────────────────────────────
        recent_p, recent_c, recent_failure_rate = self._recent_signal(node_id)

        # ── Signal 3: Semantic patterns ───────────────────────────────────────
        sem_p, sem_c, pattern_names, sem_actions = self._semantic_signal(tags)

        # ── Combine ───────────────────────────────────────────────────────────
        pairs = [
            (hist_p,   _W_HISTORICAL, hist_c),
            (recent_p, _W_RECENT,     recent_c),
            (sem_p,    _W_SEMANTIC,   sem_c),
        ]
        combined_p, combined_c = _weighted_failure_prob(pairs)

        # ── Actions ───────────────────────────────────────────────────────────
        actions = list(sem_actions)
        if combined_p >= 0.75 and "route_to_alternate_node" not in actions:
            actions.insert(0, "route_to_alternate_node")
        elif combined_p >= 0.50 and "reduce_parallelism" not in actions:
            actions.insert(0, "reduce_parallelism")

        return InfrastructureForecast(
            node_id=node_id,
            task=task,
            context_tags=tags,
            failure_probability=combined_p,
            pressure_level=PressureLevel.from_probability(combined_p),
            confidence=combined_c,
            historical_reliability=round(1.0 - hist_p, 4),
            pattern_risk=sem_p,
            recent_failure_rate=recent_failure_rate,
            contributing_patterns=pattern_names,
            recommended_actions=actions[:6],
        )

    def forecast_all_nodes(
        self,
        task:         str,
        context_tags: Optional[List[str]] = None,
    ) -> List[InfrastructureForecast]:
        """Forecast all known nodes and return sorted by failure_probability desc."""
        infra = self._mem.infrastructure_summary()
        results = [
            self.forecast(task, n["node_id"], context_tags)
            for n in infra
        ]
        return sorted(results, key=lambda f: f.failure_probability, reverse=True)

    def best_node(
        self,
        task:         str,
        context_tags: Optional[List[str]] = None,
        exclude:      Optional[List[str]] = None,
    ) -> Optional[str]:
        """Return the node_id with the lowest predicted failure probability."""
        forecasts = self.forecast_all_nodes(task, context_tags)
        excluded  = set(exclude or [])
        for f in reversed(forecasts):   # reversed = lowest risk first
            if f.node_id not in excluded:
                return f.node_id
        return None

    # ── Signal helpers ────────────────────────────────────────────────────────

    def _historical_signal(self, node_id: str) -> tuple[float, float]:
        """Long-run unreliability + confidence."""
        episodes = self._mem.episodic.node_history(node_id)
        if len(episodes) < _MIN_NODE_EPS:
            return 0.0, 0.0
        reliability  = self._mem.node_reliability(node_id)
        unreliability = round(1.0 - reliability, 4)
        confidence   = min(len(episodes) / 10, 1.0) * 0.85 + 0.15
        return unreliability, round(min(confidence, 1.0), 4)

    def _recent_signal(self, node_id: str) -> tuple[float, float, float]:
        """Unreliability from the most recent ``_window`` episodes + raw rate."""
        episodes = self._mem.episodic.node_history(node_id)
        if not episodes:
            return 0.0, 0.0, 0.0
        # node_history already sorted newest-first
        recent = episodes[: self._window]
        failures = [e for e in recent if e.outcome != "success"]
        recent_failure_rate = round(len(failures) / len(recent), 4)
        confidence = min(len(recent) / self._window, 1.0) * 0.80 + 0.20
        return recent_failure_rate, round(min(confidence, 1.0), 4), recent_failure_rate

    def _semantic_signal(
        self, tags: List[str]
    ) -> tuple[float, float, List[str], List[str]]:
        """Risk from semantic patterns that match the context tags."""
        if not tags:
            return 0.0, 0.0, [], []
        patterns = self._mem.index.known_patterns(tags)
        if not patterns:
            return 0.0, 0.0, [], []
        avg_conf  = sum(f.confidence for f in patterns) / len(patterns)
        coverage  = min(len(patterns) / 5, 1.0)
        p_risk    = round(avg_conf * coverage * 0.55, 4)
        names     = [f.pattern for f in patterns[:4]]
        actions   = []
        for fact in patterns[:3]:
            if fact.recommended_action and fact.recommended_action not in actions:
                actions.append(fact.recommended_action)
        return p_risk, round(min(avg_conf, 1.0), 4), names, actions
