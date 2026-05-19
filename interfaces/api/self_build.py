import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from andie_backend.andie.brain.llm_router import call_llm

try:
    from andie_backend.interfaces.api.ws_state import broadcast_state as _broadcast_state
    _WS_STATE_AVAILABLE = True
except ImportError:
    _WS_STATE_AVAILABLE = False
    async def _broadcast_state(state, detail=None, meta=None):  # type: ignore[misc]
        pass

try:
    from andie_backend.interfaces.api.memory import save_episode as _save_memory_episode, save_procedural as _save_memory_procedural
    _MEMORY_AVAILABLE = True
except ImportError:
    _MEMORY_AVAILABLE = False

    def _save_memory_episode(session_id: str, summary: str, tags: list[str]) -> None:  # type: ignore[misc]
        return

    def _save_memory_procedural(skill: str, method: str, confidence: float) -> None:  # type: ignore[misc]
        return

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
SELF_BUILD_DIR = BACKEND_ROOT / "storage" / "self_build"
SKILLS_PATH = SELF_BUILD_DIR / "skills.json"
GROWTH_LOG_PATH = SELF_BUILD_DIR / "growth_log.json"

_SELF_BUILD_LOOP_TASK: asyncio.Task | None = None


def _has_image_analysis_capability() -> bool:
    try:
        from andie_backend.skills.registry import registry

        return registry.get("analyze_image") is not None or registry.get("analyze_video") is not None
    except Exception:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _default_registry() -> Dict[str, Any]:
    now = _utc_now()
    return {
        "known": [
            {"skill": "web search", "confidence": 0.9, "last_used": now.split("T")[0]},
            {"skill": "code generation", "confidence": 0.85, "last_used": now.split("T")[0]},
        ],
        "gaps": [
            {"skill": "image analysis", "priority": 1, "reason": "Cannot process visual input"},
            {"skill": "long-term memory", "priority": 2, "reason": "No persistence between sessions"},
        ],
        "queue": [
            {"skill": "image analysis", "status": "learning", "attempts": 0, "updated_at": now}
        ],
        "updated_at": now,
    }


def ensure_self_build_files() -> None:
    SELF_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    if not SKILLS_PATH.exists():
        _write_json(SKILLS_PATH, _default_registry())
    if not GROWTH_LOG_PATH.exists():
        _write_json(GROWTH_LOG_PATH, [])


def read_skill_registry() -> Dict[str, Any]:
    ensure_self_build_files()
    data = _read_json(SKILLS_PATH, _default_registry())
    if not isinstance(data, dict):
        return _default_registry()
    data.setdefault("known", [])
    data.setdefault("gaps", [])
    data.setdefault("queue", [])
    data.setdefault("updated_at", _utc_now())

    # If image analysis is now supported, remove stale bootstrap gap entries.
    if _has_image_analysis_capability():
        changed = False
        gaps = data.get("gaps") if isinstance(data.get("gaps"), list) else []
        queue = data.get("queue") if isinstance(data.get("queue"), list) else []
        known = data.get("known") if isinstance(data.get("known"), list) else []

        kept_gaps = []
        for gap in gaps:
            skill_name = str((gap or {}).get("skill", "")).strip().lower()
            if skill_name == "image analysis":
                changed = True
                continue
            kept_gaps.append(gap)
        data["gaps"] = kept_gaps

        kept_queue = []
        for item in queue:
            skill_name = str((item or {}).get("skill", "")).strip().lower()
            if skill_name == "image analysis":
                changed = True
                continue
            kept_queue.append(item)
        data["queue"] = kept_queue

        has_known = any(str((entry or {}).get("skill", "")).strip().lower() == "image analysis" for entry in known)
        if not has_known:
            known.append(
                {
                    "skill": "image analysis",
                    "confidence": 0.6,
                    "last_used": _utc_now().split("T")[0],
                }
            )
            data["known"] = known
            changed = True

        if changed:
            write_skill_registry(data)

    return data


def write_skill_registry(data: Dict[str, Any]) -> None:
    data["updated_at"] = _utc_now()
    _write_json(SKILLS_PATH, data)


def read_growth_log() -> List[Dict[str, Any]]:
    ensure_self_build_files()
    data = _read_json(GROWTH_LOG_PATH, [])
    if isinstance(data, list):
        return data
    return []


def append_growth_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    log = read_growth_log()
    record = {"date": _utc_now(), **entry}
    log.append(record)
    _write_json(GROWTH_LOG_PATH, log)
    return record


def _extract_json_payload(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}

    # Accept fenced JSON responses.
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except Exception:
            return {}

    return {}


def _as_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out
    return []


def _normalize_review_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    errors = _as_string_list(payload.get("critical_errors") or payload.get("errors"))[:3]
    missing_skills = _as_string_list(payload.get("missing_skills") or payload.get("missing_skill"))
    do_differently = _as_string_list(payload.get("do_differently") or payload.get("improvements"))

    return {
        "critical_errors": errors,
        "missing_skills": missing_skills,
        "do_differently": do_differently,
        "raw": payload,
    }


def _add_or_update_gap(registry: Dict[str, Any], skill_name: str, reason: str) -> None:
    gaps = registry.setdefault("gaps", [])
    queue = registry.setdefault("queue", [])
    normalized = skill_name.strip().lower()

    existing = None
    for gap in gaps:
        if str(gap.get("skill", "")).strip().lower() == normalized:
            existing = gap
            break

    if existing is None:
        max_priority = max([int(g.get("priority", 1) or 1) for g in gaps], default=0)
        existing = {
            "skill": skill_name,
            "priority": max_priority + 1,
            "reason": reason,
        }
        gaps.append(existing)
    else:
        existing["reason"] = reason or existing.get("reason") or "Self-review detected a gap"

    queue_item = None
    for item in queue:
        if str(item.get("skill", "")).strip().lower() == normalized:
            queue_item = item
            break

    if queue_item is None:
        queue.append(
            {
                "skill": skill_name,
                "status": "queued",
                "attempts": 0,
                "updated_at": _utc_now(),
            }
        )
    else:
        queue_item.setdefault("attempts", 0)
        if queue_item.get("status") == "completed":
            queue_item["status"] = "queued"
        queue_item["updated_at"] = _utc_now()


def _pick_top_gap(registry: Dict[str, Any]) -> Dict[str, Any] | None:
    gaps = registry.get("gaps") if isinstance(registry.get("gaps"), list) else []
    queue = registry.get("queue") if isinstance(registry.get("queue"), list) else []
    if not gaps:
        return None

    queued_skill_names = {
        str(item.get("skill", "")).strip().lower(): item
        for item in queue
        if isinstance(item, dict)
    }

    ordered = sorted(gaps, key=lambda g: int(g.get("priority", 999) or 999))
    for gap in ordered:
        name = str(gap.get("skill", "")).strip().lower()
        q_item = queued_skill_names.get(name)
        if not q_item:
            return gap
        if q_item.get("status") != "completed":
            return gap
    return ordered[0] if ordered else None


async def run_self_review(task_output: Dict[str, Any]) -> Dict[str, Any]:
    registry = read_skill_registry()
    await _broadcast_state("thinking", "self-review")
    payload_preview = json.dumps(task_output or {}, ensure_ascii=True)[:8000]

    prompt = f"""
You are ANDIE performing self-review.
Task output JSON:
{payload_preview}

Without being told what is wrong, respond as strict JSON with keys:
- critical_errors: array of up to 3 strings
- missing_skills: array of strings
- do_differently: array of strings
- summary: short string
""".strip()

    raw = await asyncio.to_thread(
        call_llm,
        prompt,
        "You are an internal self-improvement engine. Respond with valid JSON only.",
        "self-review",
        os.getenv("ANDIE_SELF_BUILD_MODEL", "gpt-4o"),
    )

    parsed = _extract_json_payload(raw if isinstance(raw, str) else str(raw))
    review = _normalize_review_payload(parsed)

    for missing_skill in review.get("missing_skills", []):
        reason = "Self-review identified this as a missing capability"
        _add_or_update_gap(registry, missing_skill, reason)

    write_skill_registry(registry)
    growth_entry = append_growth_entry(
        {
            "type": "self_review",
            "skills_added": review.get("missing_skills", []),
            "skills_improved": [],
            "failed_attempts": review.get("critical_errors", []),
            "andie_note": "; ".join(review.get("do_differently", []))[:400],
        }
    )

    await _broadcast_state("idle", "self-review complete")
    return {
        "review": review,
        "registry": registry,
        "growth_entry": growth_entry,
    }

async def run_improve() -> Dict[str, Any]:
    registry = read_skill_registry()
    gap = _pick_top_gap(registry)
    if gap is None:
        growth_entry = append_growth_entry(
            {
                "type": "improve",
                "skills_added": [],
                "skills_improved": [],
                "failed_attempts": [],
                "andie_note": "No gaps found. Improvement cycle skipped.",
            }
        )
        return {
            "gap": None,
            "result": {
                "status": "skipped",
                "reason": "No gaps available",
            },
            "registry": registry,
            "growth_entry": growth_entry,
        }

    gap_skill = str(gap.get("skill") or "unknown skill")
    gap_reason = str(gap.get("reason") or "No reason provided")
    await _broadcast_state("improving", gap_skill)

    for item in registry.get("queue", []):
        if str(item.get("skill", "")).strip().lower() == gap_skill.strip().lower():
            item["status"] = "learning"
            item["attempts"] = int(item.get("attempts", 0) or 0) + 1
            item["updated_at"] = _utc_now()

    prompt = f"""
Your top skill gap is: {gap_skill}
Reason it matters: {gap_reason}

Respond as strict JSON with keys:
- research: short string
- plan: array of strings
- implementation_test: string
- worked: array of strings
- failed: array of strings
- status: one of [completed, partial, failed]
""".strip()

    raw = await asyncio.to_thread(
        call_llm,
        prompt,
        "You are an autonomous self-improvement planner. Output JSON only.",
        "self-improve",
        os.getenv("ANDIE_SELF_BUILD_MODEL", "gpt-4o"),
    )
    result = _extract_json_payload(raw if isinstance(raw, str) else str(raw))
    status = str(result.get("status") or "partial").lower()

    completed = status == "completed"

    for item in registry.get("queue", []):
        if str(item.get("skill", "")).strip().lower() == gap_skill.strip().lower():
            item["status"] = "completed" if completed else "learning"
            item["updated_at"] = _utc_now()

    if completed:
        known = registry.setdefault("known", [])
        known_item = None
        for skill in known:
            if str(skill.get("skill", "")).strip().lower() == gap_skill.strip().lower():
                known_item = skill
                break
        if known_item is None:
            known.append(
                {
                    "skill": gap_skill,
                    "confidence": 0.6,
                    "last_used": _utc_now().split("T")[0],
                }
            )
        else:
            known_item["confidence"] = min(0.99, float(known_item.get("confidence", 0.5)) + 0.05)
            known_item["last_used"] = _utc_now().split("T")[0]

    write_skill_registry(registry)
    growth_entry = append_growth_entry(
        {
            "type": "improve",
            "skills_added": [gap_skill] if completed else [],
            "skills_improved": [f"{gap_skill}: status={status}"],
            "failed_attempts": _as_string_list(result.get("failed")),
            "andie_note": str(result.get("implementation_test") or "No implementation details provided")[:400],
        }
    )

    if _MEMORY_AVAILABLE:
        method = str(
            result.get("implementation_test")
            or result.get("research")
            or "No method captured"
        )[:500]
        confidence = 0.8 if completed else 0.55 if status == "partial" else 0.25
        outcome_bits = _as_string_list(result.get("worked")) or _as_string_list(result.get("failed"))
        outcome_note = outcome_bits[0] if outcome_bits else f"status={status}"

        _save_memory_procedural(gap_skill, method, confidence)
        _save_memory_episode(
            session_id=f"self_build_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            summary=f"Attempted to improve {gap_skill}. Outcome: {outcome_note}",
            tags=["self_build", gap_skill],
        )

    await _broadcast_state("improved" if completed else "idle", f"{gap_skill}: {status}")
    return {
        "gap": gap,
        "result": result,
        "registry": registry,
        "growth_entry": growth_entry,
    }

def self_build_enabled() -> bool:
    return _env_bool("ANDIE_SELF_BUILD_ENABLED", True)


def self_review_after_task_enabled() -> bool:
    return _env_bool("ANDIE_SELF_REVIEW_AFTER_TASK", True)


def self_build_interval_seconds() -> int:
    raw = os.getenv("ANDIE_SELF_BUILD_INTERVAL_SECONDS", "86400").strip()
    try:
        return max(60, int(raw))
    except Exception:
        return 86400


async def _self_build_loop() -> None:
    interval = self_build_interval_seconds()
    while True:
        try:
            await asyncio.sleep(interval)
            await run_self_review({"output": "Scheduled self-build cycle"})
            await run_improve()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep the loop alive even if one cycle fails.
            await asyncio.sleep(5)


async def start_self_build_loop() -> None:
    global _SELF_BUILD_LOOP_TASK
    ensure_self_build_files()
    if not self_build_enabled():
        return
    if _SELF_BUILD_LOOP_TASK and not _SELF_BUILD_LOOP_TASK.done():
        return
    _SELF_BUILD_LOOP_TASK = asyncio.create_task(_self_build_loop(), name="andie-self-build-loop")


async def stop_self_build_loop() -> None:
    global _SELF_BUILD_LOOP_TASK
    if _SELF_BUILD_LOOP_TASK and not _SELF_BUILD_LOOP_TASK.done():
        _SELF_BUILD_LOOP_TASK.cancel()
        try:
            await _SELF_BUILD_LOOP_TASK
        except asyncio.CancelledError:
            pass
    _SELF_BUILD_LOOP_TASK = None
