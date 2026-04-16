from __future__ import annotations

from typing import Any, Dict, List

from autonomy.learning_engine import score_skill


DECISION_PROFILES: Dict[str, Dict[str, float]] = {
    "conservative": {
        "strategy_confidence": 0.35,
        "data_quality": 0.35,
        "risk_penalty": 0.30,
    },
    "balanced": {
        "strategy_confidence": 0.50,
        "data_quality": 0.30,
        "risk_penalty": 0.20,
    },
    "aggressive": {
        "strategy_confidence": 0.65,
        "data_quality": 0.20,
        "risk_penalty": 0.15,
    },
}


def _bounded(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n:
        return default
    return n


def _execution_for_score(score: float) -> str:
    if score > 0.80:
        return "buy_strong"
    if score > 0.70:
        return "buy"
    if score > 0.60:
        return "accumulate_small"
    if score > 0.50:
        return "hold"
    return "wait"


def _resolve_profile(profile: Any, time_horizon: Any) -> str:
    profile_name = str(profile or "").strip().lower()
    if profile_name in DECISION_PROFILES:
        return profile_name

    horizon = str(time_horizon or "").strip().lower()
    if horizon in {"short_term", "short", "intraday", "scalp"}:
        return "aggressive"
    if horizon in {"long_term", "long", "position"}:
        return "conservative"
    return "balanced"


def _compute_score(confidence: float, quality: float, risk: float, weights: Dict[str, float]) -> float:
    return _bounded(
        (confidence * weights["strategy_confidence"]) +
        (quality * weights["data_quality"]) -
        (risk * weights["risk_penalty"])
    )


def decision_profiles() -> Dict[str, Dict[str, float]]:
    return {key: dict(value) for key, value in DECISION_PROFILES.items()}


def compute_score(strategy: Dict[str, Any], data: Dict[str, Any], profile: str = "balanced") -> Dict[str, Any]:
    profile_name = _resolve_profile(profile, None)
    weights = DECISION_PROFILES[profile_name]
    confidence = _bounded(_to_float(strategy.get("confidence"), 0.0))
    risk_score = _bounded(_to_float(strategy.get("risk_score"), 1.0))
    data_quality = _bounded(_to_float(data.get("quality_score"), 0.0))
    score = _compute_score(confidence, data_quality, risk_score, weights)
    return {
        "profile": profile_name,
        "score": round(score, 4),
        "weights": dict(weights),
    }


def evaluate_plan(plan: List[Any], base_confidence: float = 0.7, context_key: str | None = None) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for step in plan:
        if isinstance(step, dict):
            step_name = str(step.get("step") or step.get("name") or "").strip()
            step_context = str(step.get("context_key") or context_key or "").strip() or None
        else:
            step_name = str(step).strip()
            step_context = context_key

        if not step_name:
            continue

        learned_score = score_skill(step_name, context_key=step_context)
        final_score = max(0.0, min(base_confidence * learned_score, 1.0))
        scored.append(
            {
                "step": step_name,
                "context_key": step_context,
                "base_confidence": round(base_confidence, 3),
                "learned_score": round(learned_score, 3),
                "confidence": round(final_score, 3),
            }
        )

    total = sum(float(entry.get("confidence", 0.0) or 0.0) for entry in scored)
    for entry in scored:
        normalized = (float(entry.get("confidence", 0.0) or 0.0) / total) if total > 0 else 0.0
        entry["normalized"] = round(normalized, 4)
    return scored


def evaluate_overseer_decision(
    *,
    confidence: Any,
    risk_score: Any,
    data_quality: Any,
    data_coverage: Any,
    volatility: Any,
    profile: Any = "balanced",
    time_horizon: Any = None,
    confidence_threshold: float,
    max_risk_score: float,
    min_data_quality: float,
) -> Dict[str, Any]:
    c = _bounded(_to_float(confidence, 0.0))
    r = _bounded(_to_float(risk_score, 1.0))
    dq = _bounded(_to_float(data_quality, 0.0))
    coverage = _bounded(_to_float(data_coverage, 0.0))
    vol = _bounded(_to_float(volatility, 1.0))

    profile_name = _resolve_profile(profile, time_horizon)
    weights = DECISION_PROFILES[profile_name]

    base_score = _compute_score(c, dq, r, weights)
    # Small market-shape adjustments that remain profile-independent.
    composite_score = _bounded(base_score + (coverage * 0.05) - (vol * 0.03))

    risk_adjusted = r <= max_risk_score
    quality_ok = dq >= min_data_quality
    confidence_ok = c >= confidence_threshold

    final_confidence = _bounded((0.70 * composite_score) + (0.30 * c))

    reason_trace: List[str] = []
    reason_trace.append(f"data_quality {'high' if dq >= 0.75 else 'moderate' if dq >= min_data_quality else 'low'} ({dq:.2f})")
    reason_trace.append(f"data_coverage {'strong' if coverage >= 0.7 else 'limited'} ({coverage:.2f})")
    reason_trace.append(f"volatility {'elevated' if vol >= 0.5 else 'contained'} ({vol:.2f})")
    reason_trace.append(f"strategy_confidence {'strong' if c >= confidence_threshold else 'below_threshold'} ({c:.2f})")
    reason_trace.append(f"risk {'acceptable' if risk_adjusted else 'too_high'} ({r:.2f})")

    risk_guardrail_triggered = r > 0.70
    execution = _execution_for_score(composite_score)
    decision = "approve" if (composite_score >= 0.60 and confidence_ok and quality_ok and risk_adjusted and not risk_guardrail_triggered) else "hold"
    if risk_guardrail_triggered:
        execution = "hold"
        reason_trace.append("risk guardrail triggered (risk_score > 0.70)")
    elif decision != "approve":
        execution = "hold" if composite_score > 0.50 else "wait"

    notes = "meets risk-adjusted decision criteria" if decision == "approve" else "fails confidence/risk/data criteria"

    return {
        "decision": decision,
        "execution": execution,
        "profile": profile_name,
        "final_confidence": round(final_confidence, 4),
        "composite_score": round(composite_score, 4),
        "risk_adjusted": risk_adjusted,
        "risk_guardrail_triggered": risk_guardrail_triggered,
        "weights": dict(weights),
        "notes": notes,
        "reason_trace": reason_trace,
        "signals": {
            "strategy_confidence": round(c, 4),
            "risk_score": round(r, 4),
            "data_quality": round(dq, 4),
            "data_coverage": round(coverage, 4),
            "volatility": round(vol, 4),
        },
    }
