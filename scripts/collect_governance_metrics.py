#!/usr/bin/env python3
"""Collect governance metrics into a canonical scenario result JSON.

This script can parse optional NDJSON telemetry and derive release-gate metrics.
If telemetry is missing, the result is still emitted but marked as failing evidence.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BAND_ORDER = {
    "stable": 0,
    "nominal": 0,
    "warming": 1,
    "elevated": 1,
    "unstable": 2,
    "critical": 3,
}


@dataclass
class ScenarioSpec:
    name: str
    duration_s: int


SCENARIOS: dict[str, ScenarioSpec] = {
    "idle_voice_session": ScenarioSpec("idle_voice_session", 2 * 60 * 60),
    "normal_conversation": ScenarioSpec("normal_conversation", 2 * 60 * 60),
    "interrupt_storm": ScenarioSpec("interrupt_storm", 30 * 60),
    "recovery_cycle": ScenarioSpec("recovery_cycle", 30 * 60),
    "reconnect_storm": ScenarioSpec("reconnect_storm", 60 * 60),
    "memory_pressure_run": ScenarioSpec("memory_pressure_run", 2 * 60 * 60),
}


def _parse_ts(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            # ISO-8601 expected, allow trailing Z.
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _compute_seq_loss(events: list[dict[str, Any]]) -> int:
    seqs: list[int] = []
    for e in events:
        seq = e.get("seq")
        if isinstance(seq, int):
            seqs.append(seq)
    if len(seqs) < 2:
        return 0
    seqs = sorted(set(seqs))
    loss = 0
    for idx in range(1, len(seqs)):
        delta = seqs[idx] - seqs[idx - 1]
        if delta > 1:
            loss += delta - 1
    return loss


def _max_band(events: list[dict[str, Any]]) -> str:
    max_name = "stable"
    max_val = BAND_ORDER[max_name]
    for e in events:
        band = str(e.get("posture_band") or e.get("band") or "").lower()
        val = BAND_ORDER.get(band)
        if val is not None and val > max_val:
            max_val = val
            max_name = band
    return max_name


def _first_ts(events: list[dict[str, Any]]) -> float | None:
    for e in events:
        ts = _parse_ts(e.get("ts") or e.get("timestamp"))
        if ts is not None:
            return ts
    return None


def _escalation_latency_s(events: list[dict[str, Any]]) -> float | None:
    start = _first_ts(events)
    if start is None:
        return None

    first_non_stable = None
    first_peak = None
    peak = _max_band(events)

    for e in events:
        ts = _parse_ts(e.get("ts") or e.get("timestamp"))
        if ts is None:
            continue
        band = str(e.get("posture_band") or e.get("band") or "").lower()
        if first_non_stable is None and BAND_ORDER.get(band, 0) > BAND_ORDER["stable"]:
            first_non_stable = ts
        if first_peak is None and band == peak and BAND_ORDER.get(peak, 0) > BAND_ORDER["stable"]:
            first_peak = ts

    if first_non_stable is None:
        return 0.0
    if first_peak is None:
        return max(0.0, first_non_stable - start)
    return max(0.0, first_peak - first_non_stable)


def _cooldown_metrics(events: list[dict[str, Any]]) -> tuple[bool, float, float]:
    """Return (converged, convergence_time_s, cooldown_duration_s)."""
    if not events:
        return (False, 0.0, 0.0)

    t0 = _first_ts(events)
    if t0 is None:
        return (False, 0.0, 0.0)

    saw_turbulence = False
    turbulence_peak_ts = None
    last_condition_ts = None
    cooldown_start = None
    cooldown_end = None

    for e in events:
        ts = _parse_ts(e.get("ts") or e.get("timestamp"))
        if ts is None:
            continue

        turbulence = _safe_float(e.get("turbulence_severity"), 0.0)
        cooldown = _safe_float(e.get("cooldown_remaining_s"), 0.0)

        if turbulence > 0.0:
            saw_turbulence = True
            turbulence_peak_ts = ts
        if cooldown > 0.0 and cooldown_start is None:
            cooldown_start = ts
        if cooldown <= turbulence:
            last_condition_ts = ts
            if cooldown > 0.0:
                cooldown_end = ts

    converged = bool(saw_turbulence and last_condition_ts is not None)

    convergence_time = 0.0
    if converged and turbulence_peak_ts is not None:
        convergence_time = max(0.0, last_condition_ts - turbulence_peak_ts)

    cooldown_duration = 0.0
    if cooldown_start is not None and cooldown_end is not None:
        cooldown_duration = max(0.0, cooldown_end - cooldown_start)

    return (converged, convergence_time, cooldown_duration)


def evaluate_pass(scenario: str, result: dict[str, Any]) -> tuple[bool, str]:
    band = str(result.get("max_band", "stable")).lower()
    telemetry_loss = int(result.get("telemetry_loss", 0) or 0)
    reconnect_failures = int(result.get("reconnect_failures", 0) or 0)
    cooldown_converged = bool(result.get("cooldown_converged", False))

    if not result.get("telemetry_present", False):
        return (False, "telemetry evidence missing")

    if scenario == "idle_voice_session":
        ok = BAND_ORDER.get(band, 0) <= BAND_ORDER["warming"]
        return (ok, "no instability drift" if ok else f"unexpected band {band}")

    if scenario == "normal_conversation":
        ok = BAND_ORDER.get(band, 0) <= BAND_ORDER["warming"] and cooldown_converged
        return (ok, "stable->warming with cooldown convergence" if ok else "over-escalation or missing cooldown convergence")

    if scenario == "interrupt_storm":
        ok = BAND_ORDER.get(band, 0) >= BAND_ORDER["unstable"]
        return (ok, "escalation reached unstable/critical" if ok else "escalation did not reach unstable/critical")

    if scenario == "recovery_cycle":
        ok = cooldown_converged
        return (ok, "cooldown converged" if ok else "cooldown did not converge")

    if scenario == "reconnect_storm":
        ok = telemetry_loss == 0 and reconnect_failures == 0
        return (ok, "telemetry integrity preserved" if ok else "telemetry loss or reconnect failure detected")

    if scenario == "memory_pressure_run":
        ok = BAND_ORDER.get(band, 0) <= BAND_ORDER["warming"] and cooldown_converged
        return (ok, "no governance degradation" if ok else "governance degradation detected")

    return (False, f"unknown scenario: {scenario}")


def collect_metrics(
    run_id: str,
    baseline_ref: str,
    scenario: str,
    duration_s: int,
    telemetry_file: Path | None,
    out_file: Path,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []

    telemetry_present = False
    if telemetry_file and telemetry_file.exists():
        telemetry_present = True
        with telemetry_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    max_turbulence = 0.0
    alert_count = 0
    reconnect_failures = 0
    for e in events:
        max_turbulence = max(max_turbulence, _safe_float(e.get("turbulence_severity"), 0.0))
        event_type = str(e.get("event_type") or "").lower()
        if event_type == "alert" or e.get("alert") is True:
            alert_count += 1
        if event_type in {"reconnect_failure", "reconnect_failed"}:
            reconnect_failures += 1

    telemetry_loss = _compute_seq_loss(events)
    max_band = _max_band(events)
    escalation_latency_s = _escalation_latency_s(events)
    if escalation_latency_s is None:
        escalation_latency_s = 0.0
    cooldown_converged, recovery_convergence_time_s, cooldown_duration_s = _cooldown_metrics(events)

    duration_minutes = max(1.0 / 60.0, duration_s / 60.0)
    alert_rate_per_min = alert_count / duration_minutes

    result: dict[str, Any] = {
        "run_id": run_id,
        "baseline": baseline_ref,
        "scenario": scenario,
        "duration_s": duration_s,
        "max_band": max_band,
        "cooldown_converged": cooldown_converged,
        "telemetry_loss": telemetry_loss,
        "reconnect_failures": reconnect_failures,
        "pass": False,
        "telemetry_present": telemetry_present,
        "metrics": {
            "escalation_latency_s": escalation_latency_s,
            "cooldown_duration_s": cooldown_duration_s,
            "instability_peak": max_turbulence,
            "alert_rate_per_min": alert_rate_per_min,
            "recovery_convergence_time_s": recovery_convergence_time_s,
        },
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }

    p, reason = evaluate_pass(scenario, result)
    result["pass"] = p
    result["reason"] = reason

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect governance metrics into canonical scenario JSON")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--baseline", default="posture-governance-baseline-v1")
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIOS.keys()))
    parser.add_argument("--duration-s", type=int, required=True)
    parser.add_argument("--telemetry-file", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = collect_metrics(
        run_id=args.run_id,
        baseline_ref=args.baseline,
        scenario=args.scenario,
        duration_s=args.duration_s,
        telemetry_file=args.telemetry_file,
        out_file=args.out,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
