"""
STEP 12B — Risk Engine
=======================
Computes a pre-execution RiskAssessment for a task by synthesising signals
from three sources:

    1. Episodic failure rate   — "How often has this task failed historically?"
    2. Semantic pattern risk   — "Do any known risk patterns match the context?"
    3. Node unreliability      — "How reliable is the target infrastructure?"

Weighting
---------
The three signals are blended as a weighted average of their failure
probabilities, adjusted by each signal's own confidence:

    combined = Σ(p_i * w_i * c_i) / Σ(w_i * c_i)

Default weights (tunable at construction time):
    episodic  → 0.40
    semantic  → 0.35
    node      → 0.25

Failure modes
-------------
The engine extracts likely failure modes from the most recent failure episodes
and from the ``reason`` field of semantic risk patterns (e.g. patterns whose
recommended action starts with ``avoid_`` or ``reduce_``).

Pre-emptive actions
-------------------
Recommended actions are drawn from:
  - Semantic patterns whose recommended_action addresses known failure modes
  - A built-in heuristic table for common high-risk scenarios

Usage
-----
    from cognition.memory import MemoryRetriever
    from cognition.prediction import RiskEngine

    retriever = MemoryRetriever.from_dir("/var/andie/memory")
    engine    = RiskEngine(retriever)

    assessment = engine.assess("deploy_api",
                                context_tags=["gpu_pressure", "high_load"],
                                node_id="nuc-main",
                                task_id="wave-03-deploy-api")
    if assessment.is_high_risk():
        # preempt before execution
        ...
"""

from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from cognition.memory import MemoryRetriever
from .prediction_models import (
    InfrastructureForecast, PressureLevel, RiskAssessment, RiskFactor,
)

# Default signal weights
_W_EPISODIC = 0.40
_W_SEMANTIC  = 0.35
_W_NODE      = 0.25

# Minimum episodes before the episodic signal is trusted
_MIN_EPISODES = 3

# Semantic patterns whose recommended_action indicates preemptive action
_PREEMPTIVE_ACTION_PREFIXES = (
    "reduce_", "avoid_", "retry_with_", "route_to_", "scale_down_",
    "throttle_", "fallback_", "delay_",
)

# Heuristic failure-mode → preemptive action table
_HEURISTIC_ACTIONS: Dict[str, str] = {
    "oom":              "reduce_parallelism_or_scope",
    "memory_pressure":  "reduce_parallelism_or_scope",
    "gpu_pressure":     "reduce_parallelism",
    "timeout":          "increase_timeout_or_split",
    "syntax_error":     "run_llm_regen_before_deploy",
    "network":          "retry_with_backoff",
    "auth":             "refresh_credentials",
    "disk":             "free_space_first",
    "cpu_pressure":     "reduce_concurrency",
    "exit_code":        "inspect_logs_before_retry",
}


def _weighted_combine(pairs: List[tuple]) -> tuple:
    """Weighted average of (probability, weight, confidence) pairs.

    Returns (combined_probability, combined_confidence).
    """
    total_weight = sum(w * c for _, w, c in pairs)
    if total_weight == 0.0:
        return 0.0, 0.0
    combined_p = sum(p * w * c for p, w, c in pairs) / total_weight
    combined_c = sum(w * c for _, w, c in pairs) / sum(w for _, w, _ in pairs)
    return round(min(combined_p, 1.0), 4), round(min(combined_c, 1.0), 4)


def _extract_failure_modes(reasons: List[str]) -> List[str]:
    """Normalise raw failure reason strings into mode labels."""
    modes: List[str] = []
    for r in reasons:
        if not r:
            continue
        rl = r.lower()
        for key in _HEURISTIC_ACTIONS:
            if key in rl and key not in modes:
                modes.append(key)
        # Include raw reason as-is if not already covered
        cleaned = r.strip()[:60]
        if cleaned and cleaned not in modes:
            modes.append(cleaned)
    return modes[:8]   # cap for readability


def _heuristic_actions_for(modes: List[str]) -> List[str]:
    """Map failure modes to heuristic preemptive actions."""
    actions: List[str] = []
    for m in modes:
        m_lower = m.lower()
        for key, action in _HEURISTIC_ACTIONS.items():
            if key in m_lower and action not in actions:
                actions.append(action)
    return actions


class RiskEngine:
    """Synthesises memory signals into a pre-execution RiskAssessment.

    Parameters
    ----------
    retriever:
        Unified memory interface (MemoryRetriever).
    w_episodic, w_semantic, w_node:
        Signal weights.  Must be > 0.  They are normalised internally.
    high_risk_threshold:
        Probability at which ``is_high_risk()`` returns True.
    """

    def __init__(
        self,
        retriever:           MemoryRetriever,
        w_episodic:          float = _W_EPISODIC,
        w_semantic:          float = _W_SEMANTIC,
        w_node:              float = _W_NODE,
        high_risk_threshold: float = 0.70,
    ) -> None:
        self._mem   = retriever
        self._we    = w_episodic
        self._ws    = w_semantic
        self._wn    = w_node
        self._thresh = high_risk_threshold

    # ── Public API ────────────────────────────────────────────────────────────

    def assess(
        self,
        task:         str,
        context_tags: Optional[List[str]] = None,
        node_id:      Optional[str]       = None,
        agent_id:     Optional[str]       = None,
        task_id:      Optional[str]       = None,
    ) -> RiskAssessment:
        """Compute a full RiskAssessment for `task` before execution."""
        tags = list(context_tags or [])
        tid  = task_id or f"risk-{uuid.uuid4().hex[:8]}"

        factors:      List[RiskFactor] = []
        failure_reasons: List[str]    = []

        # ── Signal 1: Episodic failure rate ──────────────────────────────────
        ep_p, ep_c = self._episodic_signal(task, failure_reasons)
        factors.append(RiskFactor(
            name="episodic_failure_rate",
            probability=ep_p,
            weight=self._we,
            confidence=ep_c,
            source="episodic_memory",
            detail=f"historical failure rate for '{task}'",
        ))

        # ── Signal 2: Semantic pattern risk ───────────────────────────────────
        sem_p, sem_c, sem_actions, sem_patterns = self._semantic_signal(tags, failure_reasons)
        factors.append(RiskFactor(
            name="semantic_pattern_risk",
            probability=sem_p,
            weight=self._ws,
            confidence=sem_c,
            source="semantic_memory",
            detail=f"matching risk patterns: {sem_patterns}",
        ))

        # ── Signal 3: Node unreliability ──────────────────────────────────────
        node_p, node_c = self._node_signal(node_id)
        factors.append(RiskFactor(
            name="node_unreliability",
            probability=node_p,
            weight=self._wn if node_id else 0.0,
            confidence=node_c,
            source="infrastructure_memory",
            detail=f"node={node_id or 'unspecified'}",
        ))

        # ── Combine ───────────────────────────────────────────────────────────
        pairs = [(f.probability, f.weight, f.confidence) for f in factors]
        combined_p, combined_c = _weighted_combine(pairs)

        # ── Failure modes + preemptive actions ────────────────────────────────
        modes   = _extract_failure_modes(failure_reasons)
        actions = _heuristic_actions_for(modes)
        # Add semantic-derived actions
        for a in sem_actions:
            if a not in actions:
                actions.append(a)
        # Always suggest best recovery from memory
        mem_rec = self._mem.suggest_recovery(task)
        if mem_rec and mem_rec not in actions:
            actions.insert(0, mem_rec)

        return RiskAssessment(
            task_id=tid,
            task=task,
            context_tags=tags,
            node_id=node_id,
            agent_id=agent_id,
            predicted_failure_probability=combined_p,
            confidence=combined_c,
            pressure_level=PressureLevel.from_probability(combined_p),
            risk_factors=factors,
            likely_failure_modes=modes[:6],
            recommended_preemptive_actions=actions[:6],
        )

    # ── Signal helpers ────────────────────────────────────────────────────────

    def _episodic_signal(
        self, task: str, failure_reasons: List[str]
    ) -> tuple[float, float]:
        """Failure probability + confidence from episodic history."""
        em = self._mem.episodic
        episodes = em.for_task(task)
        if not episodes:
            return 0.0, 0.0   # no data → no contribution

        # Collect failure reasons
        for ep in em.failures_for(task):
            if ep.reason:
                failure_reasons.append(ep.reason)

        failure_rate = 1.0 - em.success_rate(task)  # success_rate already rounded
        # Confidence grows with sample size, caps at 1.0 at ~30 episodes
        confidence   = min(len(episodes) / _MIN_EPISODES, 1.0) * 0.9 + 0.1
        return round(failure_rate, 4), round(min(confidence, 1.0), 4)

    def _semantic_signal(
        self, tags: List[str], failure_reasons: List[str]
    ) -> tuple[float, float, List[str], List[str]]:
        """Risk probability + preemptive actions from semantic patterns."""
        if not tags:
            return 0.0, 0.0, [], []

        patterns = self._mem.index.known_patterns(tags)
        if not patterns:
            return 0.0, 0.0, [], []

        # Aggregate confidence-weighted risk from all matching patterns
        # A pattern with high confidence suggests the action IS known — but risk
        # comes from patterns that address failure scenarios.
        # We estimate p_risk as: avg confidence of patterns * 0.5 (moderate signal)
        # because semantic facts don't store explicit failure probability.
        avg_conf = sum(f.confidence for f in patterns) / len(patterns)
        # The more patterns match, the higher the inferred risk in this context
        coverage = min(len(patterns) / 5, 1.0)
        p_risk   = round(avg_conf * coverage * 0.6, 4)

        actions  = []
        pattern_names = []
        for fact in sorted(patterns, key=lambda f: f.confidence, reverse=True)[:4]:
            pattern_names.append(fact.pattern)
            for prefix in _PREEMPTIVE_ACTION_PREFIXES:
                if fact.recommended_action.startswith(prefix):
                    if fact.recommended_action not in actions:
                        actions.append(fact.recommended_action)
            # Use pattern name as a failure reason hint
            failure_reasons.append(fact.pattern.replace("_", " "))

        return p_risk, min(avg_conf, 1.0), actions, pattern_names

    def _node_signal(
        self, node_id: Optional[str]
    ) -> tuple[float, float]:
        """Node unreliability probability + confidence."""
        if not node_id:
            return 0.0, 0.0

        reliability = self._mem.node_reliability(node_id)
        if reliability == 0.0:
            # No data OR truly 0% reliable — differentiate by episode count
            node_eps = self._mem.episodic.node_history(node_id)
            if not node_eps:
                return 0.0, 0.0   # no data
        unreliability = round(1.0 - reliability, 4)
        node_eps      = self._mem.episodic.node_history(node_id)
        confidence    = min(len(node_eps) / _MIN_EPISODES, 1.0) * 0.85 + 0.15
        return unreliability, round(min(confidence, 1.0), 4)
