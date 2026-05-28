from __future__ import annotations

import time
from .state import VoiceSessionState


def _interrupt_recency_factor(state: VoiceSessionState, now_ms: int) -> float:
    last_interrupt_ms = int(getattr(state, "last_interrupt_ts_ms", 0) or 0)
    if not last_interrupt_ms:
        return 1.0
    quiet_ms = max(0, now_ms - last_interrupt_ms)
    if quiet_ms >= 60000:
        return 0.35
    if quiet_ms >= 30000:
        return 0.55
    if quiet_ms >= 15000:
        return 0.75
    return 1.0


def _transition_recency_factor(state: VoiceSessionState, now_ms: int) -> float:
    last_transition_ms = int(getattr(state, "last_cadence_transition_ts_ms", 0) or 0)
    if not last_transition_ms:
        return 1.0
    quiet_ms = max(0, now_ms - last_transition_ms)
    if quiet_ms >= 90000:
        return 0.40
    if quiet_ms >= 45000:
        return 0.60
    if quiet_ms >= 20000:
        return 0.80
    return 1.0


def _interrupt_quiet_cooldown_factor(state: VoiceSessionState, now_ms: int) -> float:
    """Accelerate memory decay once interrupts have been quiet for a short window."""
    last_interrupt_ms = int(getattr(state, "last_interrupt_ts_ms", 0) or 0)
    if not last_interrupt_ms:
        return 1.0
    quiet_ms = max(0, now_ms - last_interrupt_ms)
    if quiet_ms >= 45000:
        return 0.05
    if quiet_ms >= 20000:
        return 0.08
    if quiet_ms >= 150:
        return 0.02
    return 1.0


def build_instability_metrics(state: VoiceSessionState) -> dict:
    now = time.time()
    now_ms = int(now * 1000)
    elapsed_min = max(1.0 / 60.0, (now - float(state.started_at)) / 60.0)

    interrupt_density = state.interrupt_count / max(1, state.completed_turns)
    total = max(1, state.cadence_transition_count_total)
    oscillation_index = min(state.cadence_transition_count_upshift, state.cadence_transition_count_downshift) / total

    transition_factor = _transition_recency_factor(state, now_ms)
    interrupt_factor = _interrupt_recency_factor(state, now_ms)
    cooldown_factor = _interrupt_quiet_cooldown_factor(state, now_ms)

    in_cooldown = cooldown_factor < 1.0
    if in_cooldown and not state.cooldown_baseline_active:
        state.cooldown_transition_baseline_total = int(state.cadence_transition_count_total)
        state.cooldown_transition_baseline_interrupt = int(state.cadence_transition_count_interrupt)
        state.cooldown_baseline_active = True
        state.cooldown_interrupt_baseline_count = int(state.interrupt_count)
        state.cooldown_completed_turns_baseline = int(state.completed_turns)
    elif not in_cooldown and state.cooldown_baseline_active:
        state.cooldown_baseline_active = False

    if in_cooldown:
        transition_count_total = max(0, int(state.cadence_transition_count_total) - int(state.cooldown_transition_baseline_total))
        transition_count_interrupt = max(0, int(state.cadence_transition_count_interrupt) - int(state.cooldown_transition_baseline_interrupt))
        cooldown_interrupts = max(0, int(state.interrupt_count) - int(state.cooldown_interrupt_baseline_count))
        cooldown_turns = max(1, int(state.completed_turns) - int(state.cooldown_completed_turns_baseline))
        interrupt_density = cooldown_interrupts / cooldown_turns
    else:
        transition_count_total = int(state.cadence_transition_count_total)
        transition_count_interrupt = int(state.cadence_transition_count_interrupt)

    transition_velocity_per_min = ((0.7 * transition_count_interrupt) + (0.3 * transition_count_total)) / elapsed_min
    effective_transition_velocity = transition_velocity_per_min * transition_factor * interrupt_factor * cooldown_factor
    effective_interrupt_density = interrupt_density * interrupt_factor * cooldown_factor

    norm_transition = min(1.0, effective_transition_velocity / 8.0)
    if in_cooldown:
        norm_transition = min(norm_transition, 0.12)
    norm_interrupt = min(1.0, effective_interrupt_density)
    if in_cooldown:
        norm_interrupt = min(norm_interrupt, 0.40)
    oscillation_decay = min(transition_factor, interrupt_factor) * cooldown_factor
    norm_oscillation = min(1.0, (oscillation_index * 2.0) * oscillation_decay)

    instability_score = min(1.0, (0.30 * norm_transition) + (0.45 * norm_interrupt) + (0.25 * norm_oscillation))
    return {
        "transition_velocity_per_min": round(effective_transition_velocity, 3),
        "interrupt_density": round(effective_interrupt_density, 3),
        "tier_oscillation_index": round(oscillation_index, 3),
        "instability_score": round(instability_score, 3),
    }


def stability_band(instability_score: float) -> str:
    if instability_score >= 0.75:
        return "critical"
    if instability_score >= 0.50:
        return "unstable"
    if instability_score >= 0.25:
        return "warming"
    return "stable"


def window_instability_summary(state: VoiceSessionState, now_ms: int, window_ms: int) -> dict:
    window_start_ms = now_ms - window_ms
    samples = [(ts, score) for ts, score in state.instability_samples if ts >= window_start_ms]
    if len(samples) < 2:
        return {"trend": "stable", "delta": 0.0, "samples": len(samples)}
    delta = float(samples[-1][1]) - float(samples[0][1])
    if delta >= 0.04:
        trend = "rising"
    elif delta <= -0.04:
        trend = "falling"
    else:
        trend = "stable"
    return {"trend": trend, "delta": round(delta, 3), "samples": len(samples)}


def session_instability_summary(state: VoiceSessionState, current_score: float, now_ms: int) -> dict:
    if not state.instability_session_baseline_ts_ms:
        state.instability_session_baseline_ts_ms = now_ms
        state.instability_session_baseline_score = float(current_score)
    delta = float(current_score) - float(state.instability_session_baseline_score or 0.0)
    if delta >= 0.10:
        trend = "rising"
    elif delta <= -0.10:
        trend = "falling"
    else:
        trend = "stable"
    return {
        "trend": trend,
        "delta": round(delta, 3),
        "samples": len(state.instability_samples),
        "baseline_ts_ms": state.instability_session_baseline_ts_ms,
    }
