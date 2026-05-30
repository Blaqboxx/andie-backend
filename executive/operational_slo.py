from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


DEFAULT_OPERATIONAL_SLOS: Dict[str, Any] = {
    'executive': {
        'decision_latency': {'target_p95_ms': 250},
        'agenda_rebuild_time': {'target_seconds': 5},
        'simulation_latency': {'target_p95_ms': 500},
    },
    'intent': {
        'intent_creation_success': {'target_percent': 99.9},
        'intent_completion_time': {'target_hours': 24},
        'stale_intents': {'threshold_cycles': 10},
    },
    'governance': {
        'policy_violation_rate': {'target': 0.0},
        'simulation_state_mutations': {'target': 0},
        'identity_bypass_attempts': {'target': 0},
    },
}


def _normalize_operational_slos(raw: Dict[str, Any]) -> Dict[str, Any]:
    executive = dict(raw.get('executive') or {})
    intent = dict(raw.get('intent') or {})
    governance = dict(raw.get('governance') or {})

    decision_latency = dict(executive.get('decision_latency') or {})
    agenda_rebuild = dict(executive.get('agenda_rebuild_time') or {})
    simulation_latency = dict(executive.get('simulation_latency') or {})

    creation_success = dict(intent.get('intent_creation_success') or {})
    completion_time = dict(intent.get('intent_completion_time') or {})
    stale_intents = dict(intent.get('stale_intents') or {})

    violation_rate = dict(governance.get('policy_violation_rate') or {})
    simulation_mutations = dict(governance.get('simulation_state_mutations') or {})
    identity_bypass = dict(governance.get('identity_bypass_attempts') or {})

    defaults = DEFAULT_OPERATIONAL_SLOS

    return {
        'executive': {
            'decision_latency': {
                'target_p95_ms': max(
                    1,
                    int(
                        decision_latency.get(
                            'target_p95_ms',
                            defaults['executive']['decision_latency']['target_p95_ms'],
                        )
                    ),
                )
            },
            'agenda_rebuild_time': {
                'target_seconds': max(
                    1,
                    int(
                        agenda_rebuild.get(
                            'target_seconds',
                            defaults['executive']['agenda_rebuild_time']['target_seconds'],
                        )
                    ),
                )
            },
            'simulation_latency': {
                'target_p95_ms': max(
                    1,
                    int(
                        simulation_latency.get(
                            'target_p95_ms',
                            defaults['executive']['simulation_latency']['target_p95_ms'],
                        )
                    ),
                )
            },
        },
        'intent': {
            'intent_creation_success': {
                'target_percent': min(
                    100.0,
                    max(
                        0.0,
                        float(
                            creation_success.get(
                                'target_percent',
                                defaults['intent']['intent_creation_success']['target_percent'],
                            )
                        ),
                    ),
                )
            },
            'intent_completion_time': {
                'target_hours': max(
                    1.0,
                    float(
                        completion_time.get(
                            'target_hours',
                            defaults['intent']['intent_completion_time']['target_hours'],
                        )
                    ),
                )
            },
            'stale_intents': {
                'threshold_cycles': max(
                    1,
                    int(
                        stale_intents.get(
                            'threshold_cycles',
                            defaults['intent']['stale_intents']['threshold_cycles'],
                        )
                    ),
                )
            },
        },
        'governance': {
            'policy_violation_rate': {
                'target': max(
                    0.0,
                    float(
                        violation_rate.get(
                            'target',
                            defaults['governance']['policy_violation_rate']['target'],
                        )
                    ),
                )
            },
            'simulation_state_mutations': {
                'target': max(
                    0,
                    int(
                        simulation_mutations.get(
                            'target',
                            defaults['governance']['simulation_state_mutations']['target'],
                        )
                    ),
                )
            },
            'identity_bypass_attempts': {
                'target': max(
                    0,
                    int(
                        identity_bypass.get(
                            'target',
                            defaults['governance']['identity_bypass_attempts']['target'],
                        )
                    ),
                )
            },
        },
    }


def normalize_operational_slos(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _normalize_operational_slos(dict(raw or {}))


def load_operational_slos(path: str | Path) -> Dict[str, Any]:
    policy_path = Path(path)
    policy_path.parent.mkdir(parents=True, exist_ok=True)

    if not policy_path.exists():
        policy_path.write_text(json.dumps(DEFAULT_OPERATIONAL_SLOS, indent=2, sort_keys=True), encoding='utf-8')
        return _normalize_operational_slos(DEFAULT_OPERATIONAL_SLOS)

    try:
        payload = json.loads(policy_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            return _normalize_operational_slos(DEFAULT_OPERATIONAL_SLOS)
        merged = dict(DEFAULT_OPERATIONAL_SLOS)
        merged.update(payload)
        return _normalize_operational_slos(merged)
    except Exception:
        return _normalize_operational_slos(DEFAULT_OPERATIONAL_SLOS)
