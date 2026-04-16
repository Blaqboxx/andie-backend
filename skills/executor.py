from __future__ import annotations

import time
from typing import Any, Dict

from autonomy.learning_engine import build_context_key
from autonomy.learning_engine import memory
from autonomy.learning_engine import score_skill
from .registry import registry


def _context_key_from_params(params: Dict[str, Any]) -> str | None:
    return build_context_key(params)


def execute_skill(skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    skill = registry.get(skill_name)
    if not skill:
        raise Exception(f"Skill {skill_name} not found")

    start = time.perf_counter()
    context_key = _context_key_from_params(params)
    try:
        result = skill.execute(params)
        latency = time.perf_counter() - start
        memory.log_execution(skill_name=skill.name, success=True, latency=latency, context_key=context_key)
        return {
            "skill": skill.name,
            "result": result,
            "risk": skill.risk_level,
            "requiresApproval": skill.requires_approval,
            "latency": round(latency, 6),
            "learning": {
                "context_key": context_key,
                "score": score_skill(skill.name, context_key=context_key),
                "executions": int(
                    memory.data.get(
                        f"{skill.name}::{context_key}" if context_key else skill.name,
                        memory.data.get(skill.name, {}),
                    ).get("executions", 0)
                    or 0
                ),
            },
        }
    except Exception as exc:
        latency = time.perf_counter() - start
        memory.log_execution(skill_name=skill.name, success=False, latency=latency, error=str(exc), context_key=context_key)
        raise


def execute_skill_plan(plan: list[str], params: Dict[str, Any]) -> Dict[str, Any]:
    results = []
    failure_count = 0
    for skill_name in plan:
        skill = registry.get(skill_name)
        if skill is None:
            raise Exception(f"Skill {skill_name} not found")
        if skill.requires_approval:
            return {
                "status": "pending_approval",
                "blockedOn": skill_name,
                "completed": results,
                "remaining": plan[len(results):],
            }
        try:
            results.append(execute_skill(skill_name, params))
        except Exception as exc:
            failure_count += 1
            results.append(
                {
                    "skill": skill_name,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            if failure_count > 2:
                return {
                    "status": "aborted_failure_cascade",
                    "reason": "failure cascade detected",
                    "failureCount": failure_count,
                    "completed": results,
                    "remaining": plan[len(results):],
                }

    return {
        "status": "ok",
        "completed": results,
        "remaining": [],
    }
