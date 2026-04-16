from __future__ import annotations

import re
from typing import Any, Dict, List

from autonomy.autonomy_controller import decide_execution_mode
from autonomy.autonomy_profiles import DEFAULT_PROFILE
from autonomy.learning_engine import skill_memory_snapshot
from autonomy.trust_engine import compute_trust

MIN_TRUST_THRESHOLD = 0.30
PROFILE_MIN_TRUST_THRESHOLD: Dict[str, float] = {
    "conservative": 0.70,
    "balanced": 0.50,
    "aggressive": 0.30,
}


def _step_name(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("step") or step.get("name") or "").strip()
    return str(step or "").strip()


def _step_context(step: Any, context_key: str | None) -> str | None:
    if isinstance(step, dict):
        return str(step.get("context_key") or context_key or "").strip() or None
    return str(context_key or "").strip() or None


def resolve_min_trust_threshold(profile: str = DEFAULT_PROFILE, override: float | None = None) -> float:
    if override is not None:
        return max(0.0, min(float(override), 1.0))
    profile_name = str(profile or "").strip().lower() or DEFAULT_PROFILE
    threshold = PROFILE_MIN_TRUST_THRESHOLD.get(profile_name, PROFILE_MIN_TRUST_THRESHOLD[DEFAULT_PROFILE])
    return max(0.0, min(float(threshold), 1.0))


def _clone_step(step: Any, trust: float | None = None) -> Dict[str, Any]:
    if isinstance(step, dict):
        payload = dict(step)
    else:
        payload = {"step": _step_name(step)}
    if trust is not None:
        payload["trust"] = round(max(0.0, min(float(trust), 1.0)), 4)
    return payload


def _tokenize(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", str(value or "").lower()) if token}


def _similarity_score(source_name: str, candidate: Dict[str, Any]) -> float:
    source_tokens = _tokenize(source_name)
    candidate_tokens = _tokenize(candidate.get("name", ""))
    for keyword in candidate.get("keywords") or []:
        candidate_tokens.update(_tokenize(str(keyword)))

    if not source_tokens or not candidate_tokens:
        return 0.0

    overlap = source_tokens.intersection(candidate_tokens)
    union = source_tokens.union(candidate_tokens)
    return len(overlap) / len(union)


def _context_match_score(context_key: str | None, candidate: Dict[str, Any]) -> float:
    context_tokens = _tokenize(context_key or "")
    if not context_tokens:
        return 1.0

    candidate_tokens = _tokenize(candidate.get("name", ""))
    for keyword in candidate.get("keywords") or []:
        candidate_tokens.update(_tokenize(str(keyword)))
    for tag in candidate.get("context_tags") or []:
        candidate_tokens.update(_tokenize(str(tag)))

    if not candidate_tokens:
        return 0.0

    overlap = context_tokens.intersection(candidate_tokens)
    return len(overlap) / len(context_tokens)


def _replacement_badge(snapshot: Dict[str, Any]) -> str:
    outcomes = snapshot.get("replacement_outcomes") or {}
    total = int(outcomes.get("total", 0) or 0)
    rate = snapshot.get("replacement_success_rate")
    if total >= 3 and rate is not None and rate >= 0.7:
        return "Proven Replacement"
    if total >= 3 and rate is not None and rate < 0.4:
        return "Unreliable"
    return "Emerging"


def suggest_alternatives(
    step_name: str,
    candidate_skills: List[Dict[str, Any]],
    context_key: str | None = None,
    exclude: set[str] | None = None,
    top_k: int = 3,
    context_match_min: float = 0.6,
) -> List[Dict[str, Any]]:
    blocked = {str(item).strip() for item in (exclude or set()) if str(item).strip()}
    scored: List[Dict[str, Any]] = []

    for candidate in candidate_skills or []:
        name = str(candidate.get("name") or "").strip()
        if not name or name in blocked:
            continue

        similarity = _similarity_score(step_name, candidate)
        context_match = _context_match_score(context_key, candidate)
        if context_match < max(0.0, min(float(context_match_min), 1.0)):
            continue

        trust = compute_trust(name, context_key=context_key, replaced_from=step_name)
        snapshot = skill_memory_snapshot(name, context_key=context_key, replaced_from=step_name)
        outcome_rate = snapshot.get("replacement_success_rate")
        pair_rate = snapshot.get("pair_success_rate")
        outcome_signal = 0.5 if outcome_rate is None else outcome_rate
        pair_signal = 0.5 if pair_rate is None else pair_rate
        risk_bonus = 1.0 if str(candidate.get("risk") or "").strip().lower() == "low" else 0.0
        score = (
            (0.35 * similarity)
            + (0.25 * trust)
            + (0.15 * context_match)
            + (0.12 * outcome_signal)
            + (0.08 * pair_signal)
            + (0.05 * risk_bonus)
        )
        confidence = (
            (0.35 * trust)
            + (0.25 * context_match)
            + (0.20 * similarity)
            + (0.10 * outcome_signal)
            + (0.10 * pair_signal)
        )

        scored.append(
            {
                "skill": name,
                "score": round(score, 4),
                "similarity": round(similarity, 4),
                "context_match": round(context_match, 4),
                "trust": round(trust, 4),
                "confidence": round(max(0.0, min(confidence, 1.0)), 4),
                "outcome_score": snapshot.get("replacement_outcome_score"),
                "pair_score": snapshot.get("pair_score"),
                "replacement_success_rate": outcome_rate,
                "pair_success_rate": pair_rate,
                "replacement_outcomes": snapshot.get("replacement_outcomes") or {},
                "pair_outcome": snapshot.get("replacement_pair") or {},
                "replacement_badge": _replacement_badge(snapshot),
                "risk": str(candidate.get("risk") or "unknown"),
                "requires_approval": bool(candidate.get("requires_approval", False)),
                "depends_on": list(candidate.get("depends_on") or []),
            }
        )

    scored.sort(key=lambda item: (item["score"], item["trust"]), reverse=True)
    return scored[: max(1, int(top_k))]


def apply_replacements(
    kept: List[Any],
    pruned: List[Dict[str, Any]],
    candidate_skills: List[Dict[str, Any]],
    context_key: str | None = None,
    profile: str = DEFAULT_PROFILE,
    global_mode: str = "assisted",
    fallback_depth: int = 3,
    context_match_min: float = 0.6,
) -> Dict[str, Any]:
    avoided: List[Dict[str, Any]] = []
    replaced: List[Dict[str, Any]] = []
    still_pruned: List[Dict[str, Any]] = []
    next_kept = list(kept or [])

    existing_names = {
        _step_name(step)
        for step in next_kept
        if _step_name(step)
    }

    for entry in pruned or []:
        original_step = str(entry.get("step") or "").strip()
        if not original_step:
            continue

        alternatives = suggest_alternatives(
            original_step,
            candidate_skills,
            context_key=context_key,
            exclude=existing_names.union({original_step}),
            top_k=fallback_depth,
            context_match_min=context_match_min,
        )

        replacement = None
        replacement_mode = "block"
        for candidate in alternatives:
            replacement_mode = decide_execution_mode(
                {"step": candidate["skill"], "risk": candidate.get("risk")},
                context_key=context_key,
                profile=profile,
                global_mode=global_mode,
            )
            if replacement_mode != "block":
                replacement = candidate
                break

        avoided_entry = {
            **entry,
            "alternatives": alternatives,
        }
        avoided.append(avoided_entry)

        if replacement is None:
            still_pruned.append(avoided_entry)
            continue

        replacement_step = {
            "step": replacement["skill"],
            "replacement_for": original_step,
            "replacement_score": replacement["score"],
            "replacement_similarity": replacement["similarity"],
            "replacement_context_match": replacement["context_match"],
            "replacement_trust": replacement["trust"],
            "replacement_confidence": replacement["confidence"],
        }
        next_kept.append(replacement_step)
        existing_names.add(replacement["skill"])
        replaced.append(
            {
                **avoided_entry,
                "replacement": replacement,
                "replacement_mode": replacement_mode,
                "confidence": replacement["confidence"],
            }
        )

    return {
        "kept": next_kept,
        "avoided": avoided,
        "replaced": replaced,
        "pruned": still_pruned,
    }


def prune_plan_with_reasons(
    plan: List[Any],
    context_key: str | None = None,
    min_trust_threshold: float | None = None,
    profile: str = DEFAULT_PROFILE,
    global_mode: str = "assisted",
) -> Dict[str, Any]:
    kept: List[Any] = []
    pruned: List[Dict[str, Any]] = []
    trust_values: List[float] = []
    threshold = resolve_min_trust_threshold(profile=profile, override=min_trust_threshold)

    for step in plan or []:
        name = _step_name(step)
        if not name:
            continue

        if isinstance(step, dict) and step.get("required"):
            kept.append(step)
            continue

        trust = compute_trust(name, _step_context(step, context_key))
        trust_values.append(trust)
        risk = str(step.get("risk") or "") if isinstance(step, dict) else ""
        execution_mode = decide_execution_mode(
            {"step": name, "risk": risk},
            context_key=_step_context(step, context_key),
            global_mode=global_mode,
            profile=profile,
        )

        # Avoid double-penalization: only prune low-trust steps that are blocked.
        if trust >= threshold or execution_mode != "block":
            kept.append(step)
            continue

        failure_probability = round(max(0.0, min(1.0 - trust, 1.0)), 4)
        pruned.append(
            {
                **_clone_step(step, trust=trust),
                "reason": "predicted_failure",
                "execution_mode": execution_mode,
                "failure_probability": failure_probability,
            }
        )

    plan_stability = round(sum(trust_values) / len(trust_values), 4) if trust_values else 0.0
    return {
        "kept": kept,
        "pruned": pruned,
        "threshold": threshold,
        "plan_stability": plan_stability,
    }


def prune_plan(
    plan: List[Any],
    context_key: str | None = None,
    min_trust_threshold: float | None = None,
    profile: str = DEFAULT_PROFILE,
    global_mode: str = "assisted",
) -> List[Any]:
    result = prune_plan_with_reasons(
        plan,
        context_key=context_key,
        min_trust_threshold=min_trust_threshold,
        profile=profile,
        global_mode=global_mode,
    )
    return result["kept"]


def optimize_plan(plan: List[Any], context_key: str | None = None) -> List[Any]:
    def _rank(step: Any) -> float:
        name = _step_name(step)
        if not name:
            return 0.0
        return compute_trust(name, _step_context(step, context_key))

    return sorted(plan or [], key=_rank, reverse=True)
