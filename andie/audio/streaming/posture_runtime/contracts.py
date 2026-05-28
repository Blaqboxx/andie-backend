from __future__ import annotations

from .state import VoiceSessionState

_CADENCE_TIER_MIN_DWELL_MS = {
    'elevated': 7000,
    'high': 12000,
    'critical': 20000,
}


def policy_contract_from_tier(tier: str) -> dict:
    tier = str(tier or 'baseline')
    if tier == 'critical':
        return {'policy_max_response_chars': 180, 'policy_preserve_collaboration': True, 'reply_delay_ms': 40, 'tts_rate_hint': '+16%'}
    if tier == 'high':
        return {'policy_max_response_chars': 240, 'policy_preserve_collaboration': True, 'reply_delay_ms': 60, 'tts_rate_hint': '+12%'}
    if tier == 'elevated':
        return {'policy_max_response_chars': 340, 'policy_preserve_collaboration': True, 'reply_delay_ms': 120, 'tts_rate_hint': '+6%'}
    return {'policy_max_response_chars': 560, 'policy_preserve_collaboration': True, 'reply_delay_ms': 180, 'tts_rate_hint': '0%'}


def cadence_min_dwell_ms(tier: str, adaptive_extra_dwell_ms: int = 0) -> int:
    return int(_CADENCE_TIER_MIN_DWELL_MS.get(tier, 0)) + int(adaptive_extra_dwell_ms or 0)