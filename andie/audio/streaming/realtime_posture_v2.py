from __future__ import annotations

import asyncio
import base64
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from andie_backend.andie.audio.asr.engine import transcribe
from andie_backend.andie.audio.tts.engine import synthesize
from andie_backend.andie.audio.streaming.posture_runtime.alerts import emit_posture_alert
from andie_backend.andie.audio.streaming.posture_runtime.contracts import cadence_min_dwell_ms, policy_contract_from_tier
from andie_backend.andie.audio.streaming.posture_runtime.cadence import build_cadence_profile, tier_rank
from andie_backend.andie.audio.streaming.posture_runtime.drift import build_instability_metrics
from andie_backend.andie.audio.streaming.posture_runtime.governance import apply_adaptive_governance, resolve_cadence_tier
from andie_backend.andie.audio.streaming.posture_runtime.state import VoiceSessionState


def _is_active_turn(state: VoiceSessionState, turn_id: int) -> bool:
    return state.turn_id == turn_id


async def _transcribe_buffer(payload: bytes, language: Optional[str]) -> dict:
    if not payload:
        return {"text": "", "segments": [], "events": [], "language": language, "elapsed_ms": 0}
    b64 = base64.b64encode(payload).decode("ascii")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, transcribe, b64, language)
    return {
        "text": str(result.get("text") or "").strip(),
        "segments": result.get("segments", []),
        "events": result.get("events", []),
        "language": result.get("language", language),
        "elapsed_ms": result.get("elapsed_ms", 0),
    }


async def _emit_state(ws: WebSocket, state: VoiceSessionState) -> None:
    await ws.send_json(
        {
            "type": "voice.state",
            "turn_id": state.turn_id,
            "mode": state.mode,
            "chunks": state.chunks,
            "bytes": state.bytes_received,
            "auto_speak": state.auto_speak,
            "uptime_s": round(time.time() - state.started_at, 3),
            "ts": int(time.time() * 1000),
        }
    )


async def _emit_posture_contract(ws: WebSocket, *, turn_id: int, tier: str, pressure_score: float, reason: str, changed: bool, state: VoiceSessionState) -> None:
    contract = policy_contract_from_tier(tier)
    now_ms = int(time.time() * 1000)
    dwell_ms = max(0, now_ms - int(state.last_cadence_transition_ts_ms or now_ms))
    base_min_dwell_ms = cadence_min_dwell_ms(tier, 0)
    min_dwell_ms = cadence_min_dwell_ms(tier, int(state.adaptive_extra_dwell_ms or 0))
    remaining_dwell_ms = max(0, min_dwell_ms - dwell_ms)
    instability = build_instability_metrics(state)
    adaptive = apply_adaptive_governance(state, instability)
    await ws.send_json(
        {
            "type": "voice.posture.contract",
            "turn_id": turn_id,
            "pressure_tier": tier,
            "pressure_score": round(float(pressure_score or 0.0), 3),
            "policy_max_response_chars": contract["policy_max_response_chars"],
            "policy_preserve_collaboration": contract["policy_preserve_collaboration"],
            "reply_delay_ms": contract["reply_delay_ms"],
            "tts_rate_hint": contract["tts_rate_hint"],
            "changed": bool(changed),
            "reason": reason,
            "dwell_ms": dwell_ms,
            "base_min_dwell_ms": base_min_dwell_ms,
            "min_dwell_ms": min_dwell_ms,
            "remaining_dwell_ms": remaining_dwell_ms,
            "downshift_eligible": remaining_dwell_ms == 0,
            "transition_count_total": state.cadence_transition_count_total,
            "transition_count_interrupt": state.cadence_transition_count_interrupt,
            "transition_count_to_baseline": state.cadence_transition_count_to_baseline,
            "transition_count_to_elevated": state.cadence_transition_count_to_elevated,
            "transition_count_to_high": state.cadence_transition_count_to_high,
            "transition_count_upshift": state.cadence_transition_count_upshift,
            "transition_count_downshift": state.cadence_transition_count_downshift,
            "transition_velocity_per_min": instability["transition_velocity_per_min"],
            "interrupt_density": instability["interrupt_density"],
            "tier_oscillation_index": instability["tier_oscillation_index"],
            "instability_score": instability["instability_score"],
            "stability_band": adaptive["stability_band"],
            "instability_trend": adaptive["instability_trend"],
            "instability_trend_30s": adaptive["instability_trend_30s"],
            "instability_trend_5m": adaptive["instability_trend_5m"],
            "instability_trend_30m": adaptive["instability_trend_30m"],
            "instability_trend_session": adaptive["instability_trend_session"],
            "instability_delta_30s": adaptive["instability_delta_30s"],
            "instability_delta_5m": adaptive["instability_delta_5m"],
            "instability_delta_30m": adaptive["instability_delta_30m"],
            "instability_delta_session": adaptive["instability_delta_session"],
            "adaptive_extra_dwell_ms": state.adaptive_extra_dwell_ms,
            "adaptive_hysteresis_margin": round(state.adaptive_hysteresis_margin, 3),
            "adaptive_max_chars_reduction": state.adaptive_max_chars_reduction,
            "ts": now_ms,
        }
    )


async def _emit_posture_stability(ws: WebSocket, *, turn_id: int, tier: str, reason: str, changed: bool, state: VoiceSessionState) -> None:
    now_ms = int(time.time() * 1000)
    dwell_ms = max(0, now_ms - int(state.last_cadence_transition_ts_ms or now_ms))
    min_dwell_ms = cadence_min_dwell_ms(tier, int(state.adaptive_extra_dwell_ms or 0))
    remaining_dwell_ms = max(0, min_dwell_ms - dwell_ms)
    instability = build_instability_metrics(state)
    await ws.send_json(
        {
            "type": "voice.posture.stability",
            "turn_id": turn_id,
            "pressure_tier": tier,
            "stability_band": state.stability_band,
            "instability_trend": state.instability_trend,
            "instability_trend_30s": state.instability_trend_30s,
            "instability_trend_5m": state.instability_trend_5m,
            "instability_trend_30m": state.instability_trend_30m,
            "instability_trend_session": state.instability_trend_session,
            "instability_delta_30s": round(state.instability_delta_30s, 3),
            "instability_delta_5m": round(state.instability_delta_5m, 3),
            "instability_delta_30m": round(state.instability_delta_30m, 3),
            "instability_delta_session": round(state.instability_delta_session, 3),
            "instability_score": instability["instability_score"],
            "transition_velocity_per_min": instability["transition_velocity_per_min"],
            "interrupt_density": instability["interrupt_density"],
            "tier_oscillation_index": instability["tier_oscillation_index"],
            "remaining_dwell_ms": remaining_dwell_ms,
            "downshift_eligible": remaining_dwell_ms == 0,
            "adaptive_extra_dwell_ms": state.adaptive_extra_dwell_ms,
            "adaptive_hysteresis_margin": round(state.adaptive_hysteresis_margin, 3),
            "adaptive_max_chars_reduction": state.adaptive_max_chars_reduction,
            "changed": bool(changed),
            "reason": reason,
            "ts": now_ms,
        }
    )
    await emit_posture_alert(ws, turn_id=turn_id, tier=tier, reason=reason, state=state)


async def _speak_text(ws: WebSocket, text: str, turn_id: int, voice: Optional[str], rate: Optional[str], pitch: Optional[str]) -> None:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, synthesize, text, voice, rate, pitch)
    audio_b64 = str(result.get("audio") or "")
    await ws.send_json({"type": "tts.start", "turn_id": turn_id, "ts": int(time.time() * 1000)})
    if audio_b64:
        await ws.send_json({"type": "tts.chunk", "turn_id": turn_id, "audio": audio_b64, "seq": 0, "eof": True, "ts": int(time.time() * 1000)})
    await ws.send_json({"type": "tts.end", "turn_id": turn_id, "elapsed_ms": result.get("elapsed_ms", 0), "ts": int(time.time() * 1000)})


async def handle_connection(ws: WebSocket, language: Optional[str] = None) -> None:
    await ws.accept()

    state = VoiceSessionState()
    acc_buf: list[bytes] = []
    speaking_task: Optional[asyncio.Task] = None
    turn_task: Optional[asyncio.Task] = None

    await ws.send_json(
        {
            "type": "voice.ready",
            "protocol": "andie.voice.realtime.v2",
            "capabilities": [
                "listen.start",
                "chunk",
                "flush",
                "speak",
                "interrupt",
                "ping",
                "voice.cadence.profile",
                "voice.posture.contract",
                "voice.posture.stability",
                "voice.posture.alert",
            ],
            "ts": int(time.time() * 1000),
        }
    )
    await _emit_state(ws, state)

    async def _cancel_speaking() -> bool:
        nonlocal speaking_task
        if speaking_task and not speaking_task.done():
            speaking_task.cancel()
            try:
                await speaking_task
            except asyncio.CancelledError:
                pass
            speaking_task = None
            return True
        return False

    async def _cancel_turn_work() -> bool:
        nonlocal turn_task
        if turn_task and not turn_task.done():
            turn_task.cancel()
            try:
                await turn_task
            except asyncio.CancelledError:
                pass
            turn_task = None
            return True
        return False

    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=20.0)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "heartbeat", "ts": int(time.time() * 1000)})
                continue

            msg_type = str(msg.get("type") or "").strip().lower()

            if msg_type == "ping":
                await ws.send_json({"type": "pong", "ts": int(time.time() * 1000)})
                continue

            if msg_type == "listen.start":
                await _cancel_turn_work()
                await _cancel_speaking()
                state.turn_id += 1
                state.mode = "listening"
                state.chunks = 0
                state.bytes_received = 0
                state.auto_speak = bool(msg.get("auto_speak", state.auto_speak))
                acc_buf = []
                await _emit_state(ws, state)
                continue

            if msg_type == "chunk":
                audio_b64 = msg.get("audio")
                if not audio_b64:
                    await ws.send_json({"type": "error", "detail": "missing_audio"})
                    continue
                if "," in audio_b64:
                    audio_b64 = audio_b64.split(",", 1)[1]
                try:
                    raw = base64.b64decode(audio_b64)
                except Exception:
                    await ws.send_json({"type": "error", "detail": "invalid_base64_audio"})
                    continue

                acc_buf.append(raw)
                state.chunks += 1
                state.bytes_received += len(raw)
                await ws.send_json({"type": "ack", "turn_id": state.turn_id, "chunks": state.chunks, "bytes": state.bytes_received, "ts": int(time.time() * 1000)})
                continue

            if msg_type == "flush":
                provided_text = str(msg.get("text") or "").strip()
                if not provided_text and not acc_buf:
                    await ws.send_json({"type": "error", "detail": "no_audio_received"})
                    continue

                await _cancel_turn_work()
                await _cancel_speaking()
                turn_id = state.turn_id
                payload = b"".join(acc_buf)
                acc_buf = []

                async def _turn_runner() -> None:
                    try:
                        state.mode = "thinking"
                        await _emit_state(ws, state)

                        if provided_text:
                            tr = {"text": provided_text, "segments": [], "events": [], "language": language, "elapsed_ms": 0}
                        else:
                            tr = await _transcribe_buffer(payload, language)

                        if not _is_active_turn(state, turn_id):
                            return

                        text = str(tr.get("text") or "").strip()
                        await ws.send_json({"type": "transcript", "turn_id": turn_id, "text": text, "segments": tr.get("segments", []), "events": tr.get("events", []), "language": tr.get("language"), "elapsed_ms": tr.get("elapsed_ms", 0), "ts": int(time.time() * 1000)})

                        profile = build_cadence_profile(state)
                        now_ms = int(time.time() * 1000)
                        previous_tier = state.last_cadence_tier
                        tier, changed = resolve_cadence_tier(state, profile, now_ms)
                        if changed:
                            state.last_cadence_transition_ts_ms = now_ms
                            state.cadence_transition_count_total += 1
                            if tier == "baseline":
                                state.cadence_transition_count_to_baseline += 1
                            elif tier == "elevated":
                                state.cadence_transition_count_to_elevated += 1
                            elif tier == "high":
                                state.cadence_transition_count_to_high += 1
                            if tier_rank(tier) > tier_rank(previous_tier):
                                state.cadence_transition_count_upshift += 1
                            elif tier_rank(tier) < tier_rank(previous_tier):
                                state.cadence_transition_count_downshift += 1

                        await _emit_posture_contract(ws, turn_id=turn_id, tier=tier, pressure_score=profile.pressure_score, reason="turn_evaluation", changed=changed, state=state)
                        await _emit_posture_stability(ws, turn_id=turn_id, tier=tier, reason="turn_evaluation", changed=changed, state=state)
                        state.last_cadence_tier = tier
                        state.last_cadence_pressure = profile.pressure_score

                        reply_text = text or "Acknowledged."
                        contract = policy_contract_from_tier(tier)
                        max_chars = max(80, int(contract["policy_max_response_chars"]) - int(state.adaptive_max_chars_reduction or 0))
                        if len(reply_text) > max_chars:
                            reply_text = reply_text[:max_chars].rstrip() + "..."

                        delay_ms = int(contract["reply_delay_ms"])
                        if delay_ms > 0:
                            await asyncio.sleep(delay_ms / 1000.0)

                        if _is_active_turn(state, turn_id):
                            await ws.send_json({"type": "reply.text", "turn_id": turn_id, "text": reply_text, "intent": "CHAT_RESPONSE", "confidence": 0.9, "meta": {"conversation_contract": {"pressure_tier": tier, "instability_trend_30s": state.instability_trend_30s, "instability_trend_5m": state.instability_trend_5m, "instability_trend_30m": state.instability_trend_30m, "instability_trend_session": state.instability_trend_session}}, "ts": int(time.time() * 1000)})
                            state.completed_turns += 1
                            state.last_reply_ts_ms = int(time.time() * 1000)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        await ws.send_json({"type": "error", "turn_id": turn_id, "detail": f"turn_failed:{type(exc).__name__}: {exc}", "ts": int(time.time() * 1000)})
                    finally:
                        if _is_active_turn(state, turn_id):
                            state.mode = "idle"
                            await _emit_state(ws, state)

                turn_task = asyncio.create_task(_turn_runner())
                continue

            if msg_type == "speak":
                text = str(msg.get("text") or "").strip()
                if not text:
                    await ws.send_json({"type": "error", "detail": "missing_text"})
                    continue

                await _cancel_turn_work()
                await _cancel_speaking()
                state.mode = "speaking"
                await _emit_state(ws, state)

                async def _runner() -> None:
                    try:
                        await _speak_text(ws, text, state.turn_id, msg.get("voice"), msg.get("rate"), msg.get("pitch"))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        await ws.send_json({"type": "error", "turn_id": state.turn_id, "detail": f"speak_failed:{type(exc).__name__}: {exc}", "ts": int(time.time() * 1000)})
                    finally:
                        state.mode = "idle"
                        await _emit_state(ws, state)

                speaking_task = asyncio.create_task(_runner())
                continue

            if msg_type == "interrupt":
                interrupted_turn = await _cancel_turn_work()
                interrupted_speech = await _cancel_speaking()
                interrupted = interrupted_turn or interrupted_speech
                state.interrupt_count += 1
                state.last_interrupt_ts_ms = int(time.time() * 1000)
                profile = build_cadence_profile(state)
                now_ms = int(time.time() * 1000)
                prev = state.last_cadence_tier
                tier, changed = resolve_cadence_tier(state, profile, now_ms)
                if changed:
                    state.last_cadence_transition_ts_ms = now_ms
                    state.cadence_transition_count_total += 1
                    state.cadence_transition_count_interrupt += 1
                    if tier == "baseline":
                        state.cadence_transition_count_to_baseline += 1
                    elif tier == "elevated":
                        state.cadence_transition_count_to_elevated += 1
                    elif tier == "high":
                        state.cadence_transition_count_to_high += 1
                    if tier_rank(tier) > tier_rank(prev):
                        state.cadence_transition_count_upshift += 1
                    elif tier_rank(tier) < tier_rank(prev):
                        state.cadence_transition_count_downshift += 1

                await _emit_posture_contract(ws, turn_id=state.turn_id, tier=tier, pressure_score=profile.pressure_score, reason="interrupt", changed=changed, state=state)
                await _emit_posture_stability(ws, turn_id=state.turn_id, tier=tier, reason="interrupt", changed=changed, state=state)
                state.last_cadence_tier = tier
                state.last_cadence_pressure = profile.pressure_score

                state.mode = "interrupted" if interrupted else "idle"
                await ws.send_json({"type": "voice.interrupted" if interrupted else "voice.idle", "turn_id": state.turn_id, "interrupted_turn": interrupted_turn, "interrupted_speech": interrupted_speech, "ts": int(time.time() * 1000)})
                await _emit_state(ws, state)
                state.mode = "idle"
                continue

            await ws.send_json({"type": "error", "detail": f"unsupported_event:{msg_type}", "ts": int(time.time() * 1000)})

    except WebSocketDisconnect:
        return
    finally:
        if turn_task and not turn_task.done():
            turn_task.cancel()
        if speaking_task and not speaking_task.done():
            speaking_task.cancel()


__all__ = ["handle_connection"]
