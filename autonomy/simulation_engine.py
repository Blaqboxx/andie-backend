from __future__ import annotations

import random
from typing import Any, Dict, List

from autonomy.learning_engine import memory
from autonomy.memory_store import MemoryStore
from autonomy.trust_engine import compute_trust


def _step_name(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("step") or step.get("name") or "").strip()
    return str(step or "").strip()


def simulate_failure_scenario(
    plan: List[Any],
    failure_rate: float = 0.20,
    seed: int | None = None,
    context_key: str | None = None,
    predictive: bool = True,
) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    bounded_failure_rate = max(0.0, min(float(failure_rate), 1.0))

    results: List[Dict[str, Any]] = []
    for step in plan or []:
        name = _step_name(step)
        if not name:
            continue
        step_context = context_key
        if isinstance(step, dict):
            step_context = str(step.get("context_key") or context_key or "").strip() or None
        trust = compute_trust(name, step_context)
        failure_probability = (1.0 - trust) if predictive else bounded_failure_rate
        failure_probability = max(0.0, min(float(failure_probability), 1.0))
        failed = rng.random() < failure_probability
        results.append(
            {
                "step": name,
                "status": "failed" if failed else "success",
                "trust": round(trust, 4),
                "failure_probability": round(failure_probability, 4),
            }
        )
    return results


def simulate_with_feedback(
    plan: List[Any],
    failure_rate: float = 0.20,
    seed: int | None = None,
    apply_feedback: bool = False,
    context_key: str | None = None,
    memory_store: MemoryStore | None = None,
    predictive: bool = True,
) -> Dict[str, Any]:
    results = simulate_failure_scenario(
        plan,
        failure_rate=failure_rate,
        seed=seed,
        context_key=context_key,
        predictive=predictive,
    )
    target_memory = memory_store or memory

    feedback_applied = 0
    if apply_feedback:
        for item in results:
            if item.get("status") == "failed":
                target_memory.log_operator_feedback("skip", skill_name=item.get("step"), context_key=context_key)
                feedback_applied += 1

    return {
        "results": results,
        "failureRate": round(max(0.0, min(float(failure_rate), 1.0)), 3),
        "predictive": bool(predictive),
        "applyFeedback": bool(apply_feedback),
        "feedbackApplied": feedback_applied,
    }
