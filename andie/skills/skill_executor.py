from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skill_domains import SKILL_SEED_PAYLOAD

SKILLS_PATH = Path(__file__).resolve().parents[2] / "storage" / "self_build" / "skills.json"
GROWTH_LOG_PATH = Path(__file__).resolve().parents[2] / "storage" / "self_build" / "growth_log.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _append_growth_log(entry: dict[str, Any]) -> None:
    logs = _load_json(GROWTH_LOG_PATH, [])
    if not isinstance(logs, list):
        logs = []
    logs.append(entry)
    GROWTH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    GROWTH_LOG_PATH.write_text(json.dumps(logs, indent=2), encoding="utf-8")


def _domain_stub(skill_name: str, task: str, context: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "status": "stub",
        "skill": skill_name,
        "task": task,
        "message": f"{skill_name} execution stub called.",
        "context_keys": sorted(list((context or {}).keys())),
    }


def _run_development_builder(task: str, context: dict[str, Any] | None) -> dict[str, Any]:
    return _domain_stub("development_builder", task, context)


def _run_conversational_intelligence(task: str, context: dict[str, Any] | None) -> dict[str, Any]:
    return _domain_stub("conversational_intelligence", task, context)


def _run_system_awareness(task: str, context: dict[str, Any] | None) -> dict[str, Any]:
    return _domain_stub("system_awareness", task, context)


def _run_media_creative_pipeline(task: str, context: dict[str, Any] | None) -> dict[str, Any]:
    return _domain_stub("media_creative_pipeline", task, context)


def _known_seed_skill_names() -> set[str]:
    names: set[str] = set()
    for item in SKILL_SEED_PAYLOAD.get("known", []):
        if isinstance(item, dict):
            name = str(item.get("skill", "")).strip()
            if name:
                names.add(name)
    return names


def execute_skill(skill_name: str, task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    skill = str(skill_name or "").strip().lower()
    context = context or {}

    runners = {
        "development_builder": _run_development_builder,
        "conversational_intelligence": _run_conversational_intelligence,
        "system_awareness": _run_system_awareness,
        "media_creative_pipeline": _run_media_creative_pipeline,
    }

    known = _known_seed_skill_names()
    if skill not in known:
        result = {
            "status": "unknown_skill",
            "skill": skill,
            "message": "Skill is not in seeded domain list.",
        }
    else:
        runner = runners.get(skill)
        if runner is None:
            result = {
                "status": "missing_runner",
                "skill": skill,
                "message": "Skill exists but runner is not implemented.",
            }
        else:
            result = runner(task, context)

    _append_growth_log(
        {
            "date": _utc_now(),
            "type": "skill_execution",
            "skill": skill,
            "task": task,
            "status": result.get("status", "unknown"),
            "note": result.get("message", ""),
        }
    )

    return result
