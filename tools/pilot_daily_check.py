from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autonomy.governance import evaluate_go_no_go


def _parse_iso_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_recent_samples(path: Path, lookback_hours: int) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
    rows: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_iso_utc(item.get("timestamp"))
        if ts is None or ts < cutoff:
            continue
        rows.append(item)
    return rows


def _mean(values: List[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _ratio_true(values: List[bool]) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if v) / float(len(values))


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def evaluate(
    *,
    metrics: Dict[str, Any],
    samples: List[Dict[str, Any]],
    min_sample_count: int,
    min_learning_signal_density: float,
    min_replacement_success_pct: float,
    max_drift_rate: float,
    max_severe_drift_ratio: float,
    max_score_drift_spikes_delta: int,
) -> Dict[str, Any]:
    counters = (metrics or {}).get("counters") or {}

    checks: List[CheckResult] = []

    sample_count = len(samples)
    checks.append(
        CheckResult(
            name="sample_size",
            passed=sample_count >= max(1, min_sample_count),
            detail=f"samples={sample_count} (min {max(1, min_sample_count)})",
        )
    )

    rates = (metrics or {}).get("rates") or {}
    learning_signal_density = rates.get("learning_signal_density")
    if learning_signal_density is None:
        checks.append(
            CheckResult(
                name="learning_signal_density",
                passed=False,
                detail="learning_signal_density not available in control-plane rates",
            )
        )
    else:
        density = float(learning_signal_density)
        checks.append(
            CheckResult(
                name="learning_signal_density",
                passed=density >= max(0.0, min_learning_signal_density),
                detail=f"events_per_hour={density:.3f} (min {max(0.0, min_learning_signal_density):.3f})",
            )
        )

    outcome_ingestion = int(counters.get("alert_outcome_ingestion_failures", 0) or 0)
    checks.append(
        CheckResult(
            name="outcome_ingestion_failures",
            passed=outcome_ingestion == 0,
            detail=f"counter={outcome_ingestion} (expected 0)",
        )
    )

    memory_errors = int(counters.get("alert_memory_write_errors", 0) or 0)
    checks.append(
        CheckResult(
            name="memory_write_errors",
            passed=memory_errors == 0,
            detail=f"counter={memory_errors} (expected 0)",
        )
    )

    score_spikes = int(counters.get("alert_score_drift_spikes", 0) or 0)
    checks.append(
        CheckResult(
            name="score_drift_spikes",
            passed=score_spikes <= max(0, max_score_drift_spikes_delta),
            detail=f"counter={score_spikes} (max {max_score_drift_spikes_delta})",
        )
    )

    success_rates = [
        float(item.get("replacement_success_rate_pct"))
        for item in samples
        if item.get("replacement_success_rate_pct") is not None
    ]
    avg_success_rate = _mean(success_rates)
    if avg_success_rate is None:
        checks.append(
            CheckResult(
                name="replacement_success_rate",
                passed=False,
                detail="no replacement_success_rate_pct samples found in lookback window",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="replacement_success_rate",
                passed=avg_success_rate >= min_replacement_success_pct,
                detail=f"avg={avg_success_rate:.2f}% (min {min_replacement_success_pct:.2f}%)",
            )
        )

    drift_values = [
        float(item.get("drift_intensity"))
        for item in samples
        if item.get("drift_intensity") is not None
    ]
    avg_drift = _mean(drift_values)
    if avg_drift is None:
        checks.append(
            CheckResult(
                name="drift_intensity",
                passed=False,
                detail="no drift_intensity samples found in lookback window",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="drift_intensity",
                passed=True,
                detail=f"avg={avg_drift:.3f}",
            )
        )

    severe_flags = [str(item.get("drift_severity") or "").lower() == "severe" for item in samples]
    severe_ratio = _ratio_true(severe_flags)
    if severe_ratio is None:
        checks.append(
            CheckResult(
                name="severe_drift_ratio",
                passed=False,
                detail="no drift_severity samples found in lookback window",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="severe_drift_ratio",
                passed=severe_ratio <= max_severe_drift_ratio,
                detail=f"ratio={severe_ratio:.3f} (max {max_severe_drift_ratio:.3f})",
            )
        )

    governance = evaluate_go_no_go(
        {
            "replacement_success_rate": (avg_success_rate / 100.0) if avg_success_rate is not None else 0.0,
            "sample_size": sample_count,
            "real_sample_size": int(counters.get("real_outcome_events_total") or 0),
            "drift_rate": max(
                float(avg_drift or 0.0),
                float(severe_ratio or 0.0),
            ),
            "learning_density": float(learning_signal_density or 0.0),
        },
        min_sample_size=max(1, min_sample_count),
        min_replacement_success_rate=max(0.0, min(1.0, min_replacement_success_pct / 100.0)),
        max_drift_rate=max(0.0, min(1.0, max_drift_rate)),
        min_learning_density=max(0.0, min_learning_signal_density),
    )

    checks.append(
        CheckResult(
            name="governance_decision",
            passed=governance.get("decision") == "GO",
            detail=(
                f"decision={governance.get('decision')} "
                f"reasons={','.join(governance.get('reasons') or []) or 'none'}"
            ),
        )
    )

    all_passed = all(check.passed for check in checks)
    return {
        "decision": governance.get("decision"),
        "reasons": governance.get("reasons"),
        "governance": governance,
        "passed": all_passed,
        "sampleCount": len(samples),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "detail": check.detail,
            }
            for check in checks
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily pilot guardrail checker")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--samples-file", default="storage/pilot/pilot_samples.jsonl")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--min-sample-count", type=int, default=20)
    parser.add_argument("--min-learning-signal-density", type=float, default=5.0)
    parser.add_argument("--min-replacement-success-pct", type=float, default=70.0)
    parser.add_argument("--max-drift-rate", type=float, default=0.15)
    parser.add_argument("--max-severe-drift-ratio", type=float, default=0.10)
    parser.add_argument("--max-score-drift-spikes-delta", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    metrics = requests.get(
        f"{args.base_url.rstrip('/')}/metrics/control-plane",
        timeout=max(1, args.timeout),
    ).json()
    samples = _read_recent_samples(Path(args.samples_file), args.lookback_hours)

    result = evaluate(
        metrics=metrics,
        samples=samples,
        min_sample_count=args.min_sample_count,
        min_learning_signal_density=args.min_learning_signal_density,
        min_replacement_success_pct=args.min_replacement_success_pct,
        max_drift_rate=args.max_drift_rate,
        max_severe_drift_ratio=args.max_severe_drift_ratio,
        max_score_drift_spikes_delta=args.max_score_drift_spikes_delta,
    )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
