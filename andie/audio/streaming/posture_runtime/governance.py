from __future__ import annotations

import time
from .contracts import cadence_min_dwell_ms
from .drift import stability_band, window_instability_summary, session_instability_summary
from .state import VoiceSessionState

_CADENCE_TRANSITION_DEBOUNCE_MS = 1500


def _tier_rank(tier: str) -> int:
    return {"baseline": 0, "elevated": 1, "high": 2, "critical": 3}.get(str(tier or "baseline"), 0)


def resolve_cadence_tier(
    state: VoiceSessionState,
    profile,
    now_ms: int | None = None,
    apply_debounce: bool = True,
    apply_dwell: bool = True,
):
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    last_interrupt_ms = int(getattr(state, "last_interrupt_ts_ms", 0) or 0)
    interrupt_quiet_ms = (now_ms - last_interrupt_ms) if last_interrupt_ms else 10**9

    score = float(getattr(profile, "pressure_score", 0.0) or 0.0)
    margin = float(getattr(state, "adaptive_hysteresis_margin", 0.0) or 0.0)
    current = str(getattr(state, "last_cadence_tier", "baseline") or "baseline")

    if score >= (0.84 + margin):
        candidate = "critical"
    elif score >= (0.65 + margin):
        candidate = "high"
    elif score >= (0.38 + margin):
        candidate = "elevated"
    else:
        candidate = "baseline"

    if current == "critical" and score < (0.72 - margin):
        candidate = "high"
    if current == "high" and score < (0.55 - margin):
        candidate = "elevated"
    if current == "elevated" and score < (0.28 - margin):
        candidate = "baseline"

    changed = candidate != current
    if not changed:
        return current, False

    if apply_debounce:
        last_ts = int(getattr(state, "last_cadence_transition_ts_ms", 0) or 0)
        if last_ts and (now_ms - last_ts) < _CADENCE_TRANSITION_DEBOUNCE_MS:
            return current, False

    if apply_dwell and _tier_rank(candidate) < _tier_rank(current):
        dwell_ms = max(0, now_ms - int(getattr(state, "last_cadence_transition_ts_ms", now_ms) or now_ms))
        min_dwell_ms = cadence_min_dwell_ms(current, int(getattr(state, "adaptive_extra_dwell_ms", 0) or 0))
        if dwell_ms < min_dwell_ms:
            return current, False

    return candidate, True


def apply_adaptive_governance(state: VoiceSessionState, instability: dict) -> dict:
    score = float(instability.get("instability_score") or 0.0)
    interrupt_density = float(instability.get("interrupt_density") or 0.0)
    now_ms = int(time.time() * 1000)
    last_interrupt_ms = int(getattr(state, "last_interrupt_ts_ms", 0) or 0)
    interrupt_quiet_ms = (now_ms - last_interrupt_ms) if last_interrupt_ms else 10**9

    state.instability_samples.append((now_ms, float(score)))
    cutoff_ms = now_ms - 1800000
    while state.instability_samples and state.instability_samples[0][0] < cutoff_ms:
        state.instability_samples.pop(0)

    trend_30s = window_instability_summary(state, now_ms, 30000)
    trend_5m = window_instability_summary(state, now_ms, 300000)
    trend_30m = window_instability_summary(state, now_ms, 1800000)
    trend_session = session_instability_summary(state, score, now_ms)

    trend_30s_label = str(trend_30s['trend'])
    if trend_30s_label == 'stable' and getattr(state, 'cooldown_baseline_active', False):
        trend_30s_label = 'falling'

    band = stability_band(score)
    delta_now = score - float(state.last_instability_score or 0.0)
    if delta_now >= 0.05:
        trend = "rising"
    elif delta_now <= -0.05:
        trend = "falling"
    else:
        trend = "stable"

    extra_dwell_ms = 0
    hysteresis_margin = 0.0
    max_chars_reduction = 0
    if band == "critical":
        extra_dwell_ms = 6000
        hysteresis_margin = 0.08
        max_chars_reduction = 120
    elif band == "unstable":
        extra_dwell_ms = 3500
        hysteresis_margin = 0.05
        max_chars_reduction = 80
    elif band == "warming":
        extra_dwell_ms = 1500
        hysteresis_margin = 0.02
        max_chars_reduction = 40

    if trend == "rising":
        extra_dwell_ms += 1500
        hysteresis_margin = min(0.10, hysteresis_margin + 0.01)
    if trend_30s_label == "rising":
        extra_dwell_ms += 1000
        hysteresis_margin = min(0.10, hysteresis_margin + 0.005)
    if trend_5m["trend"] == "rising":
        extra_dwell_ms += 2000
        hysteresis_margin = min(0.10, hysteresis_margin + 0.01)
        max_chars_reduction = max(max_chars_reduction, 20)
    if trend_30m["trend"] == "rising":
        extra_dwell_ms += 2500
        hysteresis_margin = min(0.10, hysteresis_margin + 0.015)
        max_chars_reduction = max(max_chars_reduction, 40)
    if trend_session["trend"] == "rising":
        extra_dwell_ms += 3000
        hysteresis_margin = min(0.10, hysteresis_margin + 0.02)
        max_chars_reduction = max(max_chars_reduction, 60)

    if trend_30s_label == "falling":
        extra_dwell_ms = max(0, extra_dwell_ms - 2000)
        hysteresis_margin = max(0.0, hysteresis_margin - 0.015)
    if trend_30s_label in ("falling", "stable") and interrupt_quiet_ms >= 12000 and interrupt_density <= 0.35:
        extra_dwell_ms = max(0, extra_dwell_ms - 2500)
        hysteresis_margin = max(0.0, hysteresis_margin - 0.02)

    if interrupt_density >= 0.90:
        max_chars_reduction = max(max_chars_reduction, 100)

    state.adaptive_extra_dwell_ms = int(extra_dwell_ms)
    state.adaptive_hysteresis_margin = float(hysteresis_margin)
    state.adaptive_max_chars_reduction = int(max_chars_reduction)
    state.stability_band = band
    state.instability_trend = trend
    state.instability_trend_30s = trend_30s_label
    state.instability_trend_5m = str(trend_5m["trend"])
    state.instability_trend_30m = str(trend_30m["trend"])
    state.instability_trend_session = str(trend_session["trend"])
    state.instability_delta_30s = float(trend_30s["delta"])
    state.instability_delta_5m = float(trend_5m["delta"])
    state.instability_delta_30m = float(trend_30m["delta"])
    state.instability_delta_session = float(trend_session["delta"])
    state.last_instability_score = score

    return {
        "stability_band": band,
        "instability_trend": trend,
        "instability_trend_30s": state.instability_trend_30s,
        "instability_trend_5m": state.instability_trend_5m,
        "instability_trend_30m": state.instability_trend_30m,
        "instability_trend_session": state.instability_trend_session,
        "instability_delta_30s": state.instability_delta_30s,
        "instability_delta_5m": state.instability_delta_5m,
        "instability_delta_30m": state.instability_delta_30m,
        "instability_delta_session": state.instability_delta_session,
    }
