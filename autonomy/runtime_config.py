from __future__ import annotations

from typing import Any, Dict

from autonomy.autonomy_profiles import DEFAULT_PROFILE, PROFILES

RUNTIME_CONFIG: Dict[str, Any] = {
    "profile": DEFAULT_PROFILE,
    "exploration_rate": 0.0,
    "trust_smoothing": 0.7,
    "forced_mode": None,
    "drift_detected": False,
    "drift_reason": None,
    "drift_intensity": 0.0,
    "drift_severity": "stable",
    "outcome_weighting_enabled": True,
    "runtime_outcome_emission_enabled": True,
    "observability_alerts_enabled": True,
    "score_drift_spike_threshold": 0.25,
}


def get_runtime_config() -> Dict[str, Any]:
    return dict(RUNTIME_CONFIG)


def update_runtime_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(updates, dict):
        return dict(RUNTIME_CONFIG)

    for key, value in updates.items():
        if key == "profile":
            profile_name = str(value or "").strip().lower()
            if profile_name in PROFILES:
                RUNTIME_CONFIG["profile"] = profile_name
            continue

        if key == "exploration_rate":
            try:
                rate = float(value)
            except (TypeError, ValueError):
                continue
            RUNTIME_CONFIG["exploration_rate"] = max(0.0, min(rate, 1.0))
            continue

        if key == "trust_smoothing":
            try:
                smoothing = float(value)
            except (TypeError, ValueError):
                continue
            RUNTIME_CONFIG["trust_smoothing"] = max(0.0, min(smoothing, 1.0))
            continue

        if key == "forced_mode":
            mode = None if value in (None, "", "none") else str(value).strip().lower()
            if mode in (None, "manual", "assisted", "auto", "incident"):
                RUNTIME_CONFIG["forced_mode"] = mode
            continue

        if key == "drift_detected":
            RUNTIME_CONFIG["drift_detected"] = bool(value)
            continue

        if key == "drift_reason":
            reason = None if value in (None, "") else str(value)
            RUNTIME_CONFIG["drift_reason"] = reason
            continue

        if key == "drift_intensity":
            try:
                intensity = float(value)
            except (TypeError, ValueError):
                continue
            RUNTIME_CONFIG["drift_intensity"] = max(0.0, min(intensity, 1.0))
            continue

        if key == "drift_severity":
            severity = str(value or "").strip().lower() or "stable"
            if severity in {"stable", "mild", "moderate", "severe"}:
                RUNTIME_CONFIG["drift_severity"] = severity
            continue

        if key in {"outcome_weighting_enabled", "runtime_outcome_emission_enabled", "observability_alerts_enabled"}:
            RUNTIME_CONFIG[key] = bool(value)
            continue

        if key == "score_drift_spike_threshold":
            try:
                threshold = float(value)
            except (TypeError, ValueError):
                continue
            RUNTIME_CONFIG["score_drift_spike_threshold"] = max(0.0, min(threshold, 1.0))
            continue

    return dict(RUNTIME_CONFIG)
