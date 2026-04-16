from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict


def _num(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n:
        return default
    return n


def compute_success_rate(skill_data: Dict[str, Any]) -> float:
    executions = max(int(_num(skill_data.get("executions"), 0)), 0)
    if executions == 0:
        return 0.5
    successes = max(int(_num(skill_data.get("successes"), 0)), 0)
    return min(max(successes / executions, 0.0), 1.0)


def compute_failure_penalty(skill_data: Dict[str, Any]) -> float:
    failures = max(int(_num(skill_data.get("failures"), 0)), 0)
    if failures == 0:
        return 1.0
    return max(0.1, 1.0 - (failures * 0.1))


def compute_latency_score(skill_data: Dict[str, Any]) -> float:
    latency = max(_num(skill_data.get("avg_latency"), 0.0), 0.0)
    if latency == 0:
        return 1.0
    return max(0.2, 1.0 / (1.0 + latency))


def recency_weight(last_updated: Any) -> float:
    if not last_updated:
        return 1.0
    try:
        parsed = datetime.fromisoformat(str(last_updated))
    except Exception:
        return 1.0

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    hours = max((datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0, 0.0)
    return max(0.1, math.exp(-hours / 24.0))


def outcome_freshness_weight(last_updated: Any, half_life_hours: float = 24.0 * 14.0, minimum: float = 0.35) -> float:
    if not last_updated:
        return 1.0
    try:
        parsed = datetime.fromisoformat(str(last_updated))
    except Exception:
        return 1.0

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    age_hours = max((datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0, 0.0)
    half_life = max(float(half_life_hours), 1.0)
    decay = math.exp(-math.log(2.0) * (age_hours / half_life))
    return max(minimum, min(1.0, decay))


def confidence_boost(skill_data: Dict[str, Any]) -> float:
    executions = max(int(_num(skill_data.get("executions"), 0)), 0)
    return max(0.1, min(1.0, executions / 10.0))


def is_unstable(skill_data: Dict[str, Any]) -> bool:
    failures = max(int(_num(skill_data.get("failures"), 0)), 0)
    successes = max(int(_num(skill_data.get("successes"), 0)), 0)
    return failures > successes


def compute_outcome_score(skill_data: Dict[str, Any]) -> float:
    outcomes = skill_data.get("replacement_outcomes") or {}
    total = max(int(_num(outcomes.get("total"), 0)), 0)
    if total < 3:
        return 1.0

    success = max(int(_num(outcomes.get("success"), 0)), 0)
    rate = min(max(success / total, 0.0), 1.0)
    raw = max(0.8, min(1.2, 0.8 + (0.4 * rate)))
    freshness = outcome_freshness_weight(outcomes.get("last_updated"), half_life_hours=24.0 * 14.0, minimum=0.35)
    return 1.0 + ((raw - 1.0) * freshness)


def compute_pair_score(skill_data: Dict[str, Any], replaced_from: str | None) -> float:
    original_skill = str(replaced_from or "").strip()
    if not original_skill:
        return 1.0

    pairs = skill_data.get("replacement_pairs") or {}
    pair = pairs.get(original_skill) or {}
    total = max(int(_num(pair.get("success"), 0) + _num(pair.get("failure"), 0)), 0)
    if total < 2:
        return 1.0

    success = max(int(_num(pair.get("success"), 0)), 0)
    rate = min(max(success / total, 0.0), 1.0)
    raw = max(0.85, min(1.25, 0.85 + (0.4 * rate)))
    freshness = outcome_freshness_weight(pair.get("last_updated"), half_life_hours=24.0 * 10.0, minimum=0.30)
    return 1.0 + ((raw - 1.0) * freshness)
