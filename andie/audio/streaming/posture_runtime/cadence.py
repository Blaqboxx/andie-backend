from __future__ import annotations

from .state import CadenceProfile, VoiceSessionState


def tier_rank(tier: str) -> int:
    return {"baseline": 0, "elevated": 1, "high": 2, "critical": 3}.get(str(tier or "baseline"), 0)


def build_cadence_profile(state: VoiceSessionState) -> CadenceProfile:
    turns = max(1, state.completed_turns)
    interrupt_ratio = min(1.0, state.interrupt_count / turns)
    pressure_score = min(1.0, 0.65 * interrupt_ratio + 0.35 * min(1.0, state.cadence_transition_count_total / 10.0))
    if pressure_score >= 0.75:
        return CadenceProfile(pressure_score=pressure_score, reply_delay_ms=60, max_reply_chars=200, tts_rate_hint="+12%")
    if pressure_score >= 0.40:
        return CadenceProfile(pressure_score=pressure_score, reply_delay_ms=120, max_reply_chars=320, tts_rate_hint="+6%")
    return CadenceProfile(pressure_score=pressure_score, reply_delay_ms=180, max_reply_chars=560, tts_rate_hint="0%")
