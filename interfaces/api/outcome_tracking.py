from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import HTTPException

import andie_backend.autonomy.learning_engine as learning_engine

try:
    import autonomy.learning_engine as local_learning_engine
except Exception:  # pragma: no cover - runtime import layout dependent
    local_learning_engine = None
from andie_backend.autonomy.control_plane_metrics import control_plane_metrics
from andie_backend.autonomy.learning_engine import score_skill as _score_skill
from andie_backend.autonomy.learning_engine import skill_memory_snapshot
from andie_backend.autonomy.observability_alerts import emit_observability_alert
from andie_backend.autonomy.runtime_config import get_runtime_config


def _active_learning_engine():
    if local_learning_engine is not None:
        return local_learning_engine
    return learning_engine


def first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def derive_replaced_from(*sources: Any) -> str | None:
    for source in sources:
        if isinstance(source, dict):
            value = first_non_empty(
                source.get("replaced_from"),
                source.get("replacement_for"),
                source.get("original_skill"),
                source.get("original"),
            )
            if value:
                return value
        elif isinstance(source, str):
            value = str(source or "").strip()
            if value:
                return value
    return None


def record_skill_outcome_internal(
    skill_name: str,
    result: str,
    context_key: str | None = None,
    replaced_from: str | None = None,
    latency: float | None = None,
    error: str | None = None,
    record_execution: bool = True,
    source: str = "live",  # "live" | "synthetic"
    intent_type: str | None = None,
    governance_profile: str | None = None,
    effectiveness_score: float | None = None,
    portfolio_group: str | None = None,
) -> Dict[str, Any]:
    normalized_skill = str(skill_name or "").strip()
    normalized_result = str(result or "").strip().lower()
    if not normalized_skill:
        raise HTTPException(status_code=400, detail="skill is required")
    if normalized_result not in {"success", "failure"}:
        raise HTTPException(status_code=400, detail="result must be 'success' or 'failure'")

    normalized_source = str(source or "live").strip().lower() or "live"
    if normalized_source not in {"live", "synthetic"}:
        raise HTTPException(status_code=400, detail="source must be 'live' or 'synthetic'")
    allow_synthetic = os.environ.get("ANDIE_ALLOW_SYNTHETIC_OUTCOMES", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if normalized_source == "synthetic" and not allow_synthetic:
        raise HTTPException(
            status_code=403,
            detail="synthetic outcomes disabled; set ANDIE_ALLOW_SYNTHETIC_OUTCOMES=true in dev mode",
        )

    ctx = str(context_key or "").strip() or None
    original = str(replaced_from or "").strip() or None
    normalized_intent = first_non_empty(intent_type)
    normalized_governance = first_non_empty(governance_profile)
    normalized_portfolio = first_non_empty(portfolio_group)
    normalized_effectiveness = None if effectiveness_score is None else max(0.0, min(float(effectiveness_score), 1.0))
    engine = _active_learning_engine()
    before = engine.score_skill(normalized_skill, context_key=ctx, replaced_from=original)

    try:
        if record_execution:
            engine.memory.log_execution(
                skill_name=normalized_skill,
                success=normalized_result == "success",
                latency=float(latency or 0.0),
                error=error,
                context_key=ctx,
            )
        if original:
            engine.memory.log_replacement_outcome(
                normalized_skill,
                result=normalized_result,
                replaced_from=original,
                context_key=ctx,
            )
            control_plane_metrics.increment("replaced_step_count")
            if normalized_result == "success":
                control_plane_metrics.increment("replacement_success_count")
            else:
                control_plane_metrics.increment("replacement_failure_count")

        control_plane_metrics.increment("outcome_events_total")
        if normalized_source == "live":
            control_plane_metrics.increment("real_outcome_events_total")

        outcome_weight_update = None
        effectiveness_trend_update = None
        if normalized_intent and normalized_governance and normalized_effectiveness is not None:
            registry_snapshot = engine.memory.record_outcome_weight(
                intent_type=normalized_intent,
                governance_profile=normalized_governance,
                effectiveness_score=normalized_effectiveness,
                portfolio_group=normalized_portfolio,
            )
            outcome_weight_update = {
                "event": "coordinator.outcome_weight_updated",
                "registry": registry_snapshot,
            }
            effectiveness_trend_update = engine.memory.record_effectiveness_trend(
                intent_type=normalized_intent,
                governance_profile=normalized_governance,
                effectiveness_score=normalized_effectiveness,
                portfolio_group=normalized_portfolio,
            )

        snapshot = engine.skill_memory_snapshot(normalized_skill, context_key=ctx, replaced_from=original)
        updated = snapshot.get("score")

        config = get_runtime_config()
        threshold = float(config.get("score_drift_spike_threshold", 0.25) or 0.25)
        if updated is not None and abs(float(updated) - float(before)) >= max(0.0, min(threshold, 1.0)):
            emit_observability_alert(
                "score_drift_spike",
                "Score drift spike detected after outcome ingestion",
                severity="warning",
                metadata={
                    "skill": normalized_skill,
                    "context_key": ctx,
                    "replaced_from": original,
                    "previous_score": before,
                    "updated_score": updated,
                    "delta": round(float(updated) - float(before), 4),
                    "result": normalized_result,
                },
            )

        return {
            "recorded": True,
            "skill": normalized_skill,
            "context_key": ctx,
            "result": normalized_result,
            "replaced_from": original,
            "previous_score": before,
            "updated_score": updated,
            "source": normalized_source,
            "intent_type": normalized_intent,
            "governance_profile": normalized_governance,
            "portfolio_group": normalized_portfolio,
            "effectiveness_score": normalized_effectiveness,
            "outcome_weight_update": outcome_weight_update,
            "effectiveness_trend_update": effectiveness_trend_update,
            "snapshot": snapshot,
        }
    except Exception as exc:
        emit_observability_alert(
            "outcome_ingestion_failure",
            "Failed to ingest outcome signal",
            severity="critical",
            metadata={
                "skill": normalized_skill,
                "context_key": ctx,
                "replaced_from": original,
                "result": normalized_result,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail="Outcome ingestion failed") from exc
