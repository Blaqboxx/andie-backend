#!/usr/bin/env python3
"""Compare candidate Phase 1 run outputs against baseline outputs."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCENARIOS = [
    "idle_voice_session",
    "normal_conversation",
    "interrupt_storm",
    "recovery_cycle",
    "reconnect_storm",
    "memory_pressure_run",
]


@dataclass
class Threshold:
    warn_pct: float | None
    fail_pct: float | None


THRESHOLDS: dict[str, Threshold] = {
    "cooldown_duration_s": Threshold(warn_pct=None, fail_pct=15.0),
    "alert_rate_per_min": Threshold(warn_pct=None, fail_pct=20.0),
    "recovery_convergence_time_s": Threshold(warn_pct=10.0, fail_pct=None),
    "escalation_latency_s": Threshold(warn_pct=15.0, fail_pct=25.0),
    "instability_peak": Threshold(warn_pct=10.0, fail_pct=20.0),
}


def _load_result(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _pct_change(b: float | None, c: float | None) -> float | None:
    if b is None or c is None:
        return None
    if b == 0.0:
        if c == 0.0:
            return 0.0
        return 100.0
    return ((c - b) / abs(b)) * 100.0


def _metric_status(metric: str, pct: float | None, strict_metrics: bool) -> tuple[str, str]:
    if pct is None:
        if strict_metrics:
            return ("FAIL", "missing baseline or candidate metric")
        return ("WARN", "missing baseline or candidate metric")

    t = THRESHOLDS[metric]
    mag = abs(pct)
    if t.fail_pct is not None and mag > t.fail_pct:
        return ("FAIL", f"{metric} changed {pct:.2f}% (threshold {t.fail_pct:.2f}%)")
    if t.warn_pct is not None and mag > t.warn_pct:
        return ("WARN", f"{metric} changed {pct:.2f}% (warn {t.warn_pct:.2f}%)")
    return ("PASS", f"{metric} changed {pct:.2f}%")


def compare_runs(baseline_dir: Path, candidate_dir: Path, strict_metrics: bool = False) -> dict[str, Any]:
    drift_findings: list[dict[str, Any]] = []
    scenario_summaries: list[dict[str, Any]] = []

    candidate_pass = True

    for scenario in SCENARIOS:
        b_path = baseline_dir / scenario / "result.json"
        c_path = candidate_dir / scenario / "result.json"
        b = _load_result(b_path)
        c = _load_result(c_path)

        if c is None:
            candidate_pass = False
            scenario_summaries.append({
                "scenario": scenario,
                "status": "FAIL",
                "reason": "missing candidate result",
            })
            continue

        scenario_ok = bool(c.get("pass", False))
        if not scenario_ok:
            candidate_pass = False

        scenario_summaries.append({
            "scenario": scenario,
            "status": "PASS" if scenario_ok else "FAIL",
            "reason": c.get("reason", ""),
        })

        b_metrics = ((b or {}).get("metrics") or {})
        c_metrics = (c.get("metrics") or {})

        for metric in THRESHOLDS:
            pct = _pct_change(
                None if b is None else b_metrics.get(metric),
                c_metrics.get(metric),
            )
            status, message = _metric_status(metric, pct, strict_metrics)
            if status in {"WARN", "FAIL"}:
                drift_findings.append(
                    {
                        "scenario": scenario,
                        "metric": metric,
                        "status": status,
                        "percent_change": pct,
                        "message": message,
                    }
                )

    fail_count = sum(1 for f in drift_findings if f["status"] == "FAIL")
    warn_count = sum(1 for f in drift_findings if f["status"] == "WARN")

    release_pass = candidate_pass and fail_count == 0
    verdict = "PASS"
    if not release_pass:
        verdict = "FAIL"
    elif warn_count > 0 and not strict_metrics:
        verdict = "PASS_WITH_WARNINGS"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_dir": str(baseline_dir),
        "candidate_dir": str(candidate_dir),
        "scenario_summaries": scenario_summaries,
        "drift_findings": drift_findings,
        "drift_failures": fail_count,
        "drift_warnings": warn_count,
        "candidate_matrix_pass": candidate_pass,
        "strict_metrics": strict_metrics,
        "release_gate_verdict": verdict,
    }


def _to_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Phase 1 Comparison")
    lines.append("")
    lines.append(f"Generated: {result['generated_at']}")
    lines.append(f"Release gate verdict: {result['release_gate_verdict']}")
    lines.append("")
    lines.append("## Scenario Outcomes")
    lines.append("")
    lines.append("| Scenario | Status | Reason |")
    lines.append("|---|---|---|")
    for s in result["scenario_summaries"]:
        lines.append(f"| {s['scenario']} | {s['status']} | {s.get('reason', '')} |")
    lines.append("")
    lines.append("## Drift Findings")
    lines.append("")
    if not result["drift_findings"]:
        lines.append("No drift findings.")
    else:
        lines.append("| Scenario | Metric | Status | Details |")
        lines.append("|---|---|---|---|")
        for f in result["drift_findings"]:
            lines.append(f"| {f['scenario']} | {f['metric']} | {f['status']} | {f['message']} |")
    lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare baseline and candidate Phase 1 outputs")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="Output markdown path")
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--strict-metrics", action="store_true", help="Treat missing metrics as FAIL instead of WARN")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = compare_runs(args.baseline, args.candidate, strict_metrics=args.strict_metrics)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(_to_markdown(result), encoding="utf-8")

    json_out = args.json_out or args.out.with_suffix(".json")
    json_out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    return 0 if result["release_gate_verdict"] in {"PASS", "PASS_WITH_WARNINGS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
