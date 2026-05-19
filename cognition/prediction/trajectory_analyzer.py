"""
STEP 12F — Trajectory Analyzer
================================
Detects operational trends over time for tasks, nodes, and agents.

This is *operational trend cognition* — it answers questions like:

    "Is this node's reliability getting worse?"
    "Is this agent's confidence trending down across retries?"
    "Are we seeing more failures on this task recently?"

Algorithm
---------
The analyzer takes a time-ordered series of scalar values and classifies the
trend using a split-mean comparison:

    1. Sort observations chronologically
    2. Split into first half (older) and second half (newer)
    3. Compute mean of each half
    4. Compare: delta = second_mean - first_mean

    If delta > +threshold → IMPROVING
    If delta < -threshold → DECLINING
    Else if std_dev > volatility_threshold → VOLATILE
    Else → STABLE

    With insufficient data (< min_samples) → UNKNOWN

Slope normalisation
-------------------
The ``slope`` field is normalised to [-1, +1]:

    slope = delta / max(first_mean, 0.01)  clipped to [-1, 1]

This allows downstream consumers to apply their own thresholds.

Alert generation
----------------
DECLINING or VOLATILE trends automatically populate the ``alert`` field with a
human-readable message including the subject, magnitude, and sample count.

Usage
-----
    analyzer = TrajectoryAnalyzer(retriever)

    # Task trend (confidence over recent episodes)
    report = analyzer.analyze_task("deploy_api")
    if report.direction == TrendDirection.DECLINING:
        log.warn(report.alert)

    # Node reliability trend
    report = analyzer.analyze_node("nuc-main")

    # Agent confidence trend
    report = analyzer.analyze_agent("executor-1")

    # Full system health overview
    all_reports = analyzer.system_overview()
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from cognition.memory import MemoryRetriever
from .prediction_models import DataPoint, TrendDirection, TrajectoryReport

# Minimum sample count before a trend can be classified
_MIN_SAMPLES = 4

# Delta threshold for IMPROVING / DECLINING classification
_TREND_THRESHOLD = 0.05

# Standard deviation threshold for VOLATILE classification
_VOLATILITY_THRESHOLD = 0.18


def _std_dev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _classify_trend(
    first_mean: float,
    second_mean: float,
    std: float,
) -> tuple[TrendDirection, float, Optional[str]]:
    """Return (direction, slope, alert_msg_or_None)."""
    delta = second_mean - first_mean
    slope = round(delta / max(first_mean, 0.01), 4)
    slope = max(-1.0, min(1.0, slope))

    alert: Optional[str] = None

    if abs(delta) < _TREND_THRESHOLD:
        if std > _VOLATILITY_THRESHOLD:
            direction = TrendDirection.VOLATILE
            alert = f"High volatility (σ={std:.3f})"
        else:
            direction = TrendDirection.STABLE
    elif delta > 0:
        direction = TrendDirection.IMPROVING
    else:
        direction = TrendDirection.DECLINING
        alert = f"Declining trend: Δ={delta:+.3f} (σ={std:.3f})"

    return direction, slope, alert


class TrajectoryAnalyzer:
    """Detects operational trends for tasks, nodes, and agents.

    Parameters
    ----------
    retriever:
        Unified memory interface.
    min_samples:
        Minimum observations before classification is attempted.
    trend_threshold:
        Minimum delta between half-means to classify as IMPROVING or DECLINING.
    volatility_threshold:
        Std-dev above which a STABLE delta is reclassified as VOLATILE.
    """

    def __init__(
        self,
        retriever:           MemoryRetriever,
        min_samples:         int   = _MIN_SAMPLES,
        trend_threshold:     float = _TREND_THRESHOLD,
        volatility_threshold: float = _VOLATILITY_THRESHOLD,
    ) -> None:
        self._mem    = retriever
        self._min_n  = min_samples
        self._thresh = trend_threshold
        self._vol    = volatility_threshold

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze_task(self, task: str, n: int = 40) -> TrajectoryReport:
        """Confidence trend across the most recent N episodes of a task."""
        episodes = self._mem.episodic.for_task(task)
        if not episodes:
            return self._unknown("task", task)

        # Sorted chronologically (oldest first) — episodic.for_task returns newest-first
        chronological = list(reversed(episodes[:n]))
        series = [
            DataPoint(timestamp=ep.timestamp, value=ep.confidence)
            for ep in chronological
        ]
        return self._build_report("task", task, series)

    def analyze_node(self, node_id: str, n: int = 40) -> TrajectoryReport:
        """Reliability trend for a node over its most recent N episodes.

        Values are binary: 1.0 (success) or 0.0 (failure), smoothed via
        rolling mean of 3 to reduce noise.
        """
        episodes = self._mem.episodic.node_history(node_id)
        if not episodes:
            return self._unknown("node", node_id)

        chronological = list(reversed(episodes[:n]))
        raw = [1.0 if ep.outcome == "success" else 0.0 for ep in chronological]

        # Rolling mean of 3 for smoothing
        smoothed = self._rolling_mean(raw, window=3)
        series = [
            DataPoint(timestamp=ep.timestamp, value=v)
            for ep, v in zip(chronological[len(chronological)-len(smoothed):], smoothed)
        ]
        return self._build_report("node", node_id, series)

    def analyze_agent(self, agent_id: str, n: int = 40) -> TrajectoryReport:
        """Confidence trend for an agent across its most recent N episodes."""
        episodes = list(reversed(
            self._mem.episodic.recent(500)
        ))
        agent_eps = [e for e in episodes if e.agent_id == agent_id][-n:]
        if not agent_eps:
            return self._unknown("agent", agent_id)

        series = [
            DataPoint(timestamp=ep.timestamp, value=ep.confidence)
            for ep in agent_eps
        ]
        return self._build_report("agent", agent_id, series)

    def system_overview(self) -> Dict[str, Any]:
        """Compute trajectory reports for all known nodes and return a summary."""
        infra   = self._mem.infrastructure_summary()
        reports = [self.analyze_node(n["node_id"]) for n in infra]
        declining = [r for r in reports if r.direction == TrendDirection.DECLINING]
        volatile  = [r for r in reports if r.direction == TrendDirection.VOLATILE]
        return {
            "node_count":     len(reports),
            "declining_nodes": [r.subject_id for r in declining],
            "volatile_nodes":  [r.subject_id for r in volatile],
            "node_reports":    [r.to_dict() for r in reports],
            "alerts":          [r.alert for r in reports if r.alert],
        }

    def detect_retry_degradation(
        self,
        task:          str,
        retry_window:  int = 5,
    ) -> Optional[str]:
        """Return an alert string if the last ``retry_window`` episodes of
        *task* show strictly declining confidence (each one lower than the last).
        """
        episodes = self._mem.episodic.for_task(task)[:retry_window]
        if len(episodes) < 2:
            return None
        # Episodes are newest-first; reverse to get retry sequence
        confs = [ep.confidence for ep in reversed(episodes)]
        if all(confs[i] >= confs[i+1] for i in range(len(confs)-1)):
            return (
                f"Confidence degrading across retries for '{task}': "
                f"{[round(c,2) for c in confs]}"
            )
        return None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_report(
        self,
        subject_type: str,
        subject_id:   str,
        series:       List[DataPoint],
    ) -> TrajectoryReport:
        if len(series) < self._min_n:
            return self._unknown(subject_type, subject_id, series)

        values      = [p.value for p in series]
        mid         = len(values) // 2
        first_half  = values[:mid]
        second_half = values[mid:]

        first_mean  = round(sum(first_half)  / len(first_half),  4)
        second_mean = round(sum(second_half) / len(second_half), 4)
        overall     = round(sum(values) / len(values), 4)
        std         = round(_std_dev(values), 4)

        direction, slope, alert = _classify_trend(first_mean, second_mean, std)

        if alert:
            alert = f"[{subject_type}:{subject_id}] {alert} (n={len(series)})"

        return TrajectoryReport(
            subject_type=subject_type,
            subject_id=subject_id,
            series=series,
            direction=direction,
            slope=slope,
            volatility=std,
            first_half_mean=first_mean,
            second_half_mean=second_mean,
            overall_mean=overall,
            sample_count=len(series),
            alert=alert,
        )

    def _unknown(
        self,
        subject_type: str,
        subject_id:   str,
        series: Optional[List[DataPoint]] = None,
    ) -> TrajectoryReport:
        return TrajectoryReport(
            subject_type=subject_type,
            subject_id=subject_id,
            series=series or [],
            direction=TrendDirection.UNKNOWN,
            slope=0.0,
            volatility=0.0,
            first_half_mean=0.0,
            second_half_mean=0.0,
            overall_mean=0.0,
            sample_count=len(series) if series else 0,
            alert=None,
        )

    @staticmethod
    def _rolling_mean(values: List[float], window: int) -> List[float]:
        if len(values) < window:
            return values
        result = []
        for i in range(window - 1, len(values)):
            result.append(round(sum(values[i - window + 1: i + 1]) / window, 4))
        return result
