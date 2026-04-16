from __future__ import annotations

from typing import Any, Dict, List, Literal


def _confidence_tier(
    real_sample_size: int,
    total_sample_size: int,
) -> Literal["synthetic", "mixed", "production"]:
    """Classify evidence quality.

    synthetic   — no real-world outcomes recorded yet
    mixed       — some real outcomes but below minimum threshold
    production  — ≥20 real outcomes (fully real-traffic validated)
    """
    if real_sample_size <= 0:
        return "synthetic"
    if real_sample_size < 20:
        return "mixed"
    return "production"


def evaluate_go_no_go(
    metrics: Dict[str, Any],
    *,
    min_sample_size: int = 20,
    min_replacement_success_rate: float = 0.7,
    max_drift_rate: float = 0.15,
    min_learning_density: float = 5.0,
) -> Dict[str, Any]:
    """Return deterministic rollout decision and machine-readable reasons."""

    sample_size = int(metrics.get("sample_size", 0) or 0)
    real_sample_size = int(metrics.get("real_sample_size", 0) or 0)
    replacement_success_rate = float(metrics.get("replacement_success_rate", 0.0) or 0.0)
    drift_rate = float(metrics.get("drift_rate", 0.0) or 0.0)
    learning_density = float(metrics.get("learning_density", 0.0) or 0.0)

    reasons: List[str] = []
    if sample_size < max(1, min_sample_size):
        reasons.append("insufficient_sample_size")

    if replacement_success_rate < max(0.0, min(1.0, min_replacement_success_rate)):
        reasons.append("low_replacement_success")

    if drift_rate > max(0.0, min(1.0, max_drift_rate)):
        reasons.append("high_drift_rate")

    if learning_density < max(0.0, min_learning_density):
        reasons.append("low_signal_density")

    decision = "GO" if not reasons else "NO_GO"
    tier = _confidence_tier(real_sample_size, sample_size)

    return {
        "decision": decision,
        "confidence_tier": tier,
        "reasons": reasons,
        "metrics": {
            "replacement_success_rate": replacement_success_rate,
            "sample_size": sample_size,
            "real_sample_size": real_sample_size,
            "drift_rate": drift_rate,
            "learning_density": learning_density,
        },
        "thresholds": {
            "min_sample_size": max(1, min_sample_size),
            "min_replacement_success_rate": max(0.0, min(1.0, min_replacement_success_rate)),
            "max_drift_rate": max(0.0, min(1.0, max_drift_rate)),
            "min_learning_density": max(0.0, min_learning_density),
        },
    }
