from __future__ import annotations

import time
from fastapi import WebSocket
from .state import VoiceSessionState


def _evidence_level(state: VoiceSessionState) -> int:
    return max(
        int(getattr(state, 'completed_turns', 0) or 0),
        int(getattr(state, 'cadence_transition_count_total', 0) or 0),
        len(getattr(state, 'instability_samples', []) or []),
    )


def posture_alert_level(state: VoiceSessionState) -> str:
    evidence = _evidence_level(state)
    session_rising = state.instability_trend_session == 'rising'
    sustained_rising = state.instability_trend_30m == 'rising' and state.instability_trend_5m == 'rising'

    interrupt_transitions = int(getattr(state, 'cadence_transition_count_interrupt', 0) or 0)
    interrupt_count = int(getattr(state, 'interrupt_count', 0) or 0)
    interrupt_storm = interrupt_transitions >= 3 or interrupt_count >= 4

    if evidence < 2:
        if state.stability_band in {'critical', 'unstable'} and (
            state.instability_trend_5m == 'rising' or interrupt_storm
        ):
            return 'notice'
        return 'none'

    if evidence >= 3 and (
        (state.stability_band == 'critical' and sustained_rising and session_rising)
        or (session_rising and sustained_rising and float(state.instability_delta_session or 0.0) >= 0.12)
        or (state.stability_band == 'critical' and interrupt_transitions >= 5)
    ):
        return 'critical'

    if state.stability_band in {'critical', 'unstable'} and (
        state.instability_trend_5m == 'rising'
        or state.instability_trend_30m == 'rising'
        or session_rising
        or interrupt_storm
    ):
        return 'warn'

    if state.stability_band == 'warming' and (
        state.instability_trend_5m == 'rising'
        or state.instability_trend_30m == 'rising'
        or session_rising
    ):
        return 'notice'

    return 'none'


async def emit_posture_alert(ws: WebSocket, *, turn_id: int, tier: str, reason: str, state: VoiceSessionState) -> None:
    now_ms = int(time.time() * 1000)
    alert_level = posture_alert_level(state)
    if alert_level == 'none':
        return
    if state.last_posture_alert_level == alert_level and (now_ms - int(state.last_posture_alert_ts_ms or 0)) < 120000:
        return
    state.last_posture_alert_level = alert_level
    state.last_posture_alert_ts_ms = now_ms
    await ws.send_json(
        {
            'type': 'voice.posture.alert',
            'turn_id': turn_id,
            'pressure_tier': tier,
            'alert_level': alert_level,
            'stability_band': state.stability_band,
            'instability_trend_30s': state.instability_trend_30s,
            'instability_trend_5m': state.instability_trend_5m,
            'instability_trend_30m': state.instability_trend_30m,
            'instability_trend_session': state.instability_trend_session,
            'instability_delta_30s': round(state.instability_delta_30s, 3),
            'instability_delta_5m': round(state.instability_delta_5m, 3),
            'instability_delta_30m': round(state.instability_delta_30m, 3),
            'instability_delta_session': round(state.instability_delta_session, 3),
            'adaptive_extra_dwell_ms': state.adaptive_extra_dwell_ms,
            'adaptive_hysteresis_margin': round(state.adaptive_hysteresis_margin, 3),
            'adaptive_max_chars_reduction': state.adaptive_max_chars_reduction,
            'reason': reason,
            'ts': now_ms,
        }
    )