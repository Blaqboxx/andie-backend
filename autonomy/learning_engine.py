from __future__ import annotations

import re
from typing import Any, Dict

from autonomy.memory_store import MemoryStore
from autonomy.metrics import (
    compute_failure_penalty,
    compute_latency_score,
    compute_outcome_score,
    compute_pair_score,
    compute_success_rate,
    confidence_boost,
    is_unstable,
    recency_weight,
)
from autonomy.runtime_config import get_runtime_config

# Dampened influence cap for operator-feedback signals.
# A value of 0.2 means operator preference can shift a score by at most ±20 %
# of the base score, preventing overfitting to a single operator action.
FEEDBACK_WEIGHT = 0.2
OUTCOME_WEIGHT = 0.25
PAIR_WEIGHT = 0.15

memory = MemoryStore()


def build_context_key(context: Dict[str, Any] | None = None) -> str | None:
    if not isinstance(context, dict):
        return None

    stream_type = str(context.get("stream_type") or context.get("streamType") or "").strip().lower()
    protocol = str(context.get("protocol") or "").strip().lower()
    encoder = str(context.get("encoder_type") or context.get("encoderType") or "").strip().lower()
    region = str(context.get("region") or "").strip().lower()
    explicit = str(context.get("context_key") or context.get("contextKey") or "").strip().lower()

    if explicit:
        explicit = explicit.replace("hls_stream", "hls").replace("rtmp_stream", "rtmp")
        explicit = re.sub(r"[^a-z0-9:_|\-]+", "", explicit)
        return explicit or None

    parts = [stream_type, protocol, encoder, region]
    parts = [part for part in parts if part]
    if not parts:
        return None
    return "::".join(parts)


def _memory_key(skill_name: str, context_key: str | None = None) -> str:
    key = str(skill_name or "").strip()
    context = memory._canonicalize_context_key(context_key) or ""
    return f"{key}::{context}" if context else key


def _operator_score_adjustment(data: Dict[str, Any]) -> float:
    """Return a normalized [-1.0, 1.0] operator-preference signal.

    Positive = operator has chosen this skill as a replacement (boost).
    Negative = operator has replaced or skipped this skill (penalty).

    The signal saturates after ~10 interactions so a single outlier operator
    session cannot dominate long-term scoring.
    """
    fb = data.get("operator_feedback") or {}
    swaps_to = int(fb.get("swaps_to", 0) or 0)
    swaps_from = int(fb.get("swaps_from", 0) or 0)
    skips = int(fb.get("skips", 0) or 0)

    total = swaps_to + swaps_from + skips
    if total == 0:
        return 0.0

    # Skips are a weaker signal than a direct swap (0.5 weight)
    net = swaps_to - swaps_from - (skips * 0.5)
    # Saturation: reaches full strength at 10 interactions, stays flat beyond
    saturation = min(total / 10.0, 1.0)
    normalized = max(-1.0, min(net / max(total, 1), 1.0))
    return normalized * saturation


def score_skill(skill_name: str, context_key: str | None = None, replaced_from: str | None = None) -> float:
    key = _memory_key(skill_name, context_key)
    data = memory.data.get(key)
    if not data:
        data = memory.data.get(str(skill_name or "").strip())
    if not data:
        return 0.6

    executions = int(data.get("executions", 0) or 0)
    if executions <= 0:
        score = 0.6
    else:
        success = compute_success_rate(data)
        failure_penalty = compute_failure_penalty(data)
        latency = compute_latency_score(data)
        recency = recency_weight(data.get("last_updated"))
        confidence = confidence_boost(data)

        score = ((success * 0.5) + (failure_penalty * 0.3) + (latency * 0.2)) * recency * confidence
        if is_unstable(data):
            score *= 0.85

    # Apply bounded outcome and operator signals multiplicatively so
    # replacement evidence can shift ranking without overwhelming execution history.
    op_adj = _operator_score_adjustment(data)
    config = get_runtime_config()
    outcome_enabled = bool(config.get("outcome_weighting_enabled", True))
    outcome_mult = compute_outcome_score(data) if outcome_enabled else 1.0
    pair_mult = compute_pair_score(data, replaced_from) if outcome_enabled else 1.0
    score = score * (1.0 + FEEDBACK_WEIGHT * op_adj)
    score = score * (1.0 + OUTCOME_WEIGHT * (outcome_mult - 1.0))
    score = score * (1.0 + PAIR_WEIGHT * (pair_mult - 1.0))

    return round(max(0.05, min(score, 0.95)), 3)


def skill_memory_snapshot(skill_name: str, context_key: str | None = None, replaced_from: str | None = None) -> Dict[str, Any]:
    key = _memory_key(skill_name, context_key)
    data = memory.data.get(key) or memory.data.get(skill_name) or {}
    replacement_outcomes = data.get("replacement_outcomes") or {}
    replacement_total = int(replacement_outcomes.get("total", 0) or 0)
    replacement_success = int(replacement_outcomes.get("success", 0) or 0)
    replacement_failure = int(replacement_outcomes.get("failure", 0) or 0)
    pair_entry = {}
    pair_total = 0
    if replaced_from:
        pair_entry = (data.get("replacement_pairs") or {}).get(str(replaced_from or "").strip()) or {}
        pair_total = int((pair_entry.get("success", 0) or 0) + (pair_entry.get("failure", 0) or 0))

    replacement_success_rate = round(replacement_success / replacement_total, 4) if replacement_total else None
    pair_success_rate = round((pair_entry.get("success", 0) or 0) / pair_total, 4) if pair_total else None
    return {
        "skill": data.get("skill") or skill_name,
        "context_key": data.get("context_key"),
        "score": score_skill(skill_name, context_key=context_key, replaced_from=replaced_from),
        "executions": int(data.get("executions", 0) or 0),
        "successes": int(data.get("successes", 0) or 0),
        "failures": int(data.get("failures", 0) or 0),
        "avg_latency": round(float(data.get("avg_latency", 0.0) or 0.0), 4),
        "last_updated": data.get("last_updated"),
        "unstable": is_unstable(data) if data else False,
        "failure_signatures": data.get("failure_signatures") or {},
        "operator_feedback": data.get("operator_feedback") or {},
        "replacement_outcomes": {
            "total": replacement_total,
            "success": replacement_success,
            "failure": replacement_failure,
            "last_updated": replacement_outcomes.get("last_updated"),
        },
        "replacement_success_rate": replacement_success_rate,
        "replacement_outcome_score": round(compute_outcome_score(data), 4) if data else 1.0,
        "replacement_pair": {
            "original": str(replaced_from or "").strip() or None,
            "success": int(pair_entry.get("success", 0) or 0),
            "failure": int(pair_entry.get("failure", 0) or 0),
            "last_updated": pair_entry.get("last_updated"),
        },
        "pair_success_rate": pair_success_rate,
        "pair_score": round(compute_pair_score(data, replaced_from), 4) if data else 1.0,
    }
