from __future__ import annotations

from dataclasses import dataclass, field
import time

@dataclass
class VoiceSessionState:
    turn_id: int = 0
    mode: str = 'idle'
    chunks: int = 0
    bytes_received: int = 0
    started_at: float = field(default_factory=time.time)
    auto_speak: bool = False
    interrupt_count: int = 0
    completed_turns: int = 0
    last_reply_ts_ms: int = 0
    last_interrupt_ts_ms: int = 0
    last_cadence_tier: str = 'baseline'
    last_cadence_pressure: float = 0.0
    last_cadence_transition_ts_ms: int = 0
    cadence_transition_count_total: int = 0
    cadence_transition_count_interrupt: int = 0
    cadence_transition_count_to_baseline: int = 0
    cadence_transition_count_to_elevated: int = 0
    cadence_transition_count_to_high: int = 0
    cadence_transition_count_upshift: int = 0
    cadence_transition_count_downshift: int = 0
    adaptive_extra_dwell_ms: int = 0
    adaptive_hysteresis_margin: float = 0.0
    adaptive_max_chars_reduction: int = 0
    stability_band: str = 'stable'
    last_instability_score: float = 0.0
    instability_trend: str = 'stable'
    instability_trend_30s: str = 'stable'
    instability_trend_5m: str = 'stable'
    instability_trend_30m: str = 'stable'
    instability_trend_session: str = 'stable'
    instability_delta_30s: float = 0.0
    instability_delta_5m: float = 0.0
    instability_delta_30m: float = 0.0
    instability_delta_session: float = 0.0
    instability_session_baseline_score: float = 0.0
    instability_session_baseline_ts_ms: int = 0
    cooldown_transition_baseline_total: int = 0
    cooldown_transition_baseline_interrupt: int = 0
    cooldown_baseline_active: bool = False
    cooldown_interrupt_baseline_count: int = 0
    cooldown_completed_turns_baseline: int = 0
    last_posture_alert_level: str = 'none'
    last_posture_alert_ts_ms: int = 0
    instability_samples: list[tuple[int, float]] = field(default_factory=list)

@dataclass
class CadenceProfile:
    pressure_score: float
    reply_delay_ms: int
    max_reply_chars: int
    tts_rate_hint: str
