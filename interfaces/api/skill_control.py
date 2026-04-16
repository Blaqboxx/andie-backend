from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from autonomy.policy_audit import audit_logger
from autonomy.learning_engine import skill_memory_snapshot
from autonomy.control_plane_metrics import control_plane_metrics
from skills.router import select_skill
from skills.schemas import Skill


SETTINGS_SCHEMA_VERSION = 1
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
SETTINGS_CONFIG_PATH = BACKEND_ROOT / "storage" / "config" / "control_plane_settings.json"


def _default_settings_payload() -> Dict[str, Any]:
    return {
        "schemaVersion": SETTINGS_SCHEMA_VERSION,
        "savedAt": None,
        "updatedBy": None,
        "config": {},
    }


def _read_settings_payload() -> Dict[str, Any]:
    if not SETTINGS_CONFIG_PATH.exists():
        return _default_settings_payload()

    try:
        payload = json.loads(SETTINGS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_settings_payload()

    if not isinstance(payload, dict):
        return _default_settings_payload()

    baseline = _default_settings_payload()
    baseline["schemaVersion"] = payload.get("schemaVersion", SETTINGS_SCHEMA_VERSION)
    baseline["savedAt"] = payload.get("savedAt")
    baseline["updatedBy"] = payload.get("updatedBy")
    baseline["config"] = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    return baseline


def _write_settings_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    SETTINGS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _normalize_skill_names(values: Iterable[Any]) -> List[str]:
    normalized = sorted(
        {
            str(value).strip()
            for value in values or []
            if str(value or "").strip()
        }
    )
    return normalized


def get_skill_control_state() -> Dict[str, Any]:
    payload = _read_settings_payload()
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    skills = config.get("skills") if isinstance(config.get("skills"), dict) else {}
    controls = skills.get("controls") if isinstance(skills.get("controls"), dict) else {}
    return {
        "incident_mode": bool(controls.get("incident_mode", False)),
        "blacklisted_skills": _normalize_skill_names(controls.get("blacklisted_skills") or []),
        "updated_at": payload.get("savedAt"),
        "updated_by": payload.get("updatedBy"),
    }


def update_skill_control_state(
    *,
    incident_mode: bool | None = None,
    blacklisted_skills: Iterable[Any] | None = None,
    updated_by: str = "operator-ui",
    reason: str | None = None,
    request_id: str | None = None,
) -> Dict[str, Any]:
    normalized_reason = str(reason or "").strip()

    previous = get_skill_control_state()
    previous_blacklist = set(previous.get("blacklisted_skills") or [])

    proposed_incident_mode = previous.get("incident_mode") if incident_mode is None else bool(incident_mode)
    proposed_blacklist = previous_blacklist if blacklisted_skills is None else set(_normalize_skill_names(blacklisted_skills))

    added_blacklist = sorted(proposed_blacklist - previous_blacklist)
    removed_blacklist = sorted(previous_blacklist - proposed_blacklist)
    incident_enabled = previous.get("incident_mode") is False and proposed_incident_mode is True

    if incident_enabled and not normalized_reason:
        raise ValueError("Reason is required when enabling incident mode")
    if added_blacklist and not normalized_reason:
        raise ValueError("Reason is required when blacklisting skills")

    payload = _read_settings_payload()
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    payload["config"] = config

    skills = config.get("skills") if isinstance(config.get("skills"), dict) else {}
    config["skills"] = skills

    controls = skills.get("controls") if isinstance(skills.get("controls"), dict) else {}
    skills["controls"] = controls

    if incident_mode is not None:
        controls["incident_mode"] = bool(incident_mode)
    if blacklisted_skills is not None:
        controls["blacklisted_skills"] = _normalize_skill_names(blacklisted_skills)

    payload["schemaVersion"] = payload.get("schemaVersion") or SETTINGS_SCHEMA_VERSION
    payload["savedAt"] = datetime.now(timezone.utc).isoformat()
    payload["updatedBy"] = updated_by
    _write_settings_payload(payload)
    current = get_skill_control_state()

    current_blacklist = set(current.get("blacklisted_skills") or [])

    if previous.get("incident_mode") != current.get("incident_mode"):
        audit_logger.log_event(
            actor=updated_by,
            action="incident_mode_toggle",
            previous=bool(previous.get("incident_mode")),
            new=bool(current.get("incident_mode")),
            reason=normalized_reason or "operator_policy_change",
            request_id=request_id,
        )
        if current.get("incident_mode"):
            control_plane_metrics.increment("incident_mode_activations")

    if added_blacklist:
        audit_logger.log_event(
            actor=updated_by,
            action="blacklist_skill",
            previous=sorted(previous_blacklist),
            new=sorted(current_blacklist),
            reason=normalized_reason,
            request_id=request_id,
        )
        control_plane_metrics.increment("blacklist_activations", len(added_blacklist))

    if removed_blacklist:
        audit_logger.log_event(
            actor=updated_by,
            action="unblacklist_skill",
            previous=sorted(previous_blacklist),
            new=sorted(current_blacklist),
            reason=normalized_reason or "operator_policy_change",
            request_id=request_id,
        )

    return current


def skill_suppression_reason(skill_name: str) -> str | None:
    normalized_name = str(skill_name or "").strip()
    if not normalized_name:
        return None

    control_state = get_skill_control_state()
    if normalized_name in set(control_state.get("blacklisted_skills") or []):
        return "blacklisted"

    if control_state.get("incident_mode"):
        snapshot = skill_memory_snapshot(normalized_name)
        if snapshot.get("unstable"):
            return "incident_mode_unstable"

    return None


def list_routable_skills(skills: Iterable[Skill]) -> Tuple[List[Skill], Dict[str, str]]:
    allowed: List[Skill] = []
    suppressed: Dict[str, str] = {}
    for skill in skills:
        reason = skill_suppression_reason(skill.name)
        if reason:
            suppressed[skill.name] = reason
            continue
        allowed.append(skill)
    return allowed, suppressed


def describe_suppressed_skills(skills: Iterable[Skill]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for skill in skills:
        reason = skill_suppression_reason(skill.name)
        if not reason:
            continue
        snapshot = skill_memory_snapshot(skill.name)
        entries.append(
            {
                "skill": skill.name,
                "reason": reason,
                "unstable": bool(snapshot.get("unstable")),
                "executions": snapshot.get("executions", 0),
                "failures": snapshot.get("failures", 0),
            }
        )
    return entries


def blocked_primary_skill(task: str, skills: Iterable[Skill]) -> Dict[str, Any] | None:
    selected = select_skill(task, skills)
    if selected is None:
        return None
    reason = skill_suppression_reason(selected.name)
    if not reason:
        return None
    snapshot = skill_memory_snapshot(selected.name)
    return {
        "skill": selected.name,
        "reason": reason,
        "unstable": bool(snapshot.get("unstable")),
        "executions": snapshot.get("executions", 0),
        "failures": snapshot.get("failures", 0),
    }