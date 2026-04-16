from __future__ import annotations

import os
import random
from typing import Any, Dict, Iterable

from .graph import build_skill_graph
from .schemas import Skill


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def estimate_skill_confidence(task: str, skill: Skill) -> float:
    lowered = _normalize(task)
    if not lowered:
        return 0.0

    matches = 0
    for keyword in skill.keywords or [skill.name]:
        if _normalize(keyword) in lowered:
            matches += 1

    description_hits = 1 if any(token in lowered for token in _normalize(skill.description).split()[:4]) else 0
    base = 0.2 + (0.18 * matches) + (0.08 * description_hits)
    if skill.name in lowered:
        base += 0.25
    return max(0.0, min(0.95, base))


def select_skill(task: str, skills: Iterable[Skill]) -> Skill | None:
    skill_list = list(skills)
    if not skill_list:
        return None

    raw_rate = os.environ.get("ANDIE_SKILL_EXPLORATION_RATE", "0.0")
    try:
        exploration_rate = max(0.0, min(float(raw_rate), 1.0))
    except ValueError:
        exploration_rate = 0.0

    if exploration_rate > 0 and random.random() < exploration_rate:
        return random.choice(skill_list)

    best_skill = None
    best_score = 0.0
    for skill in skill_list:
        score = estimate_skill_confidence(task, skill)
        if score > best_score:
            best_score = score
            best_skill = skill
    return best_skill if best_score >= 0.35 else None


def build_skill_proposal(task: str, skill: Skill | None) -> Dict[str, Any]:
    if skill is None:
        return {
            "selectedSkill": None,
            "confidence": 0.0,
            "requiresApproval": False,
            "risk": None,
        }

    confidence = estimate_skill_confidence(task, skill)
    return {
        "selectedSkill": skill.name,
        "confidence": round(confidence, 4),
        "requiresApproval": skill.requires_approval,
        "risk": skill.risk_level,
    }


def build_execution_plan(task: str, skills: Iterable[Skill]) -> Dict[str, Any]:
    skill_list = list(skills)
    selected = select_skill(task, skill_list)
    proposal = build_skill_proposal(task, selected)
    if selected is None:
        proposal["plan"] = []
        return proposal

    allowed_skill_names = {skill.name for skill in skill_list}
    graph = build_skill_graph()
    plan = graph.resolve_execution_order(selected.name)
    blocked_dependencies = [skill_name for skill_name in plan if skill_name not in allowed_skill_names]
    if blocked_dependencies:
        proposal["selectedSkill"] = None
        proposal["confidence"] = 0.0
        proposal["requiresApproval"] = False
        proposal["risk"] = None
        proposal["plan"] = []
        proposal["blockedDependencies"] = blocked_dependencies
        return proposal

    if selected.name == "resync_audio" and "verify_stream_health" not in plan and "verify_stream_health" in allowed_skill_names:
        plan.append("verify_stream_health")
    proposal["plan"] = plan
    return proposal
