from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


DEFAULT_AGENDA_POLICY: Dict[str, Any] = {
    'max_deferred_cycles': 3,
    'sentinel_escalation_rate': 1.0,
    'academy_decay_rate': 1.0,
    'blocker_escalation_threshold': 3,
}


def _normalize_policy(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'max_deferred_cycles': max(1, int(raw.get('max_deferred_cycles', DEFAULT_AGENDA_POLICY['max_deferred_cycles']))),
        'sentinel_escalation_rate': max(0.0, float(raw.get('sentinel_escalation_rate', DEFAULT_AGENDA_POLICY['sentinel_escalation_rate']))),
        'academy_decay_rate': max(0.0, float(raw.get('academy_decay_rate', DEFAULT_AGENDA_POLICY['academy_decay_rate']))),
        'blocker_escalation_threshold': max(
            1,
            int(raw.get('blocker_escalation_threshold', DEFAULT_AGENDA_POLICY['blocker_escalation_threshold'])),
        ),
    }


def normalize_agenda_policy(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _normalize_policy(dict(raw or {}))


def load_agenda_policy(path: str | Path) -> Dict[str, Any]:
    policy_path = Path(path)
    policy_path.parent.mkdir(parents=True, exist_ok=True)

    if not policy_path.exists():
        policy_path.write_text(json.dumps(DEFAULT_AGENDA_POLICY, indent=2, sort_keys=True), encoding='utf-8')
        return dict(DEFAULT_AGENDA_POLICY)

    try:
        payload = json.loads(policy_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            return dict(DEFAULT_AGENDA_POLICY)
        merged = dict(DEFAULT_AGENDA_POLICY)
        merged.update(payload)
        return _normalize_policy(merged)
    except Exception:
        return dict(DEFAULT_AGENDA_POLICY)
