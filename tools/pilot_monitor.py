from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import requests


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value) * 100.0, 2)
    except Exception:
        return None


def sample(base_url: str, timeout: int = 10) -> Dict[str, Any]:
    metrics = requests.get(f"{base_url}/metrics/control-plane", timeout=timeout).json()
    drift = requests.get(f"{base_url}/autonomy/drift", timeout=timeout).json()
    feedback = requests.get(f"{base_url}/skills/feedback", timeout=timeout).json()

    rates = (metrics or {}).get("rates") or {}
    entry = {
        "timestamp": now_iso(),
        "replacement_success_rate_pct": pct(rates.get("replacement_success_rate")),
        "replacement_rate_pct": pct(rates.get("replacement_rate")),
        "auto_execution_rate_pct": pct(rates.get("auto_execution_rate")),
        "learning_signal_density": rates.get("learning_signal_density"),
        "drift_intensity": drift.get("drift_intensity"),
        "drift_severity": drift.get("drift_severity"),
        "feedback_skills": int(feedback.get("total_skills", 0) or 0),
        "alert_counters": {
            "outcome_ingestion_failures": int((metrics.get("counters") or {}).get("alert_outcome_ingestion_failures", 0) or 0),
            "score_drift_spikes": int((metrics.get("counters") or {}).get("alert_score_drift_spikes", 0) or 0),
            "memory_write_errors": int((metrics.get("counters") or {}).get("alert_memory_write_errors", 0) or 0),
        },
    }
    return entry


def append_jsonl(path: Path, item: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")


def run_loop(base_url: str, output: Path, interval_seconds: int, samples: int) -> None:
    for _ in range(samples):
        entry = sample(base_url)
        append_jsonl(output, entry)
        print(json.dumps(entry, sort_keys=True))
        time.sleep(max(1, interval_seconds))


def main() -> int:
    parser = argparse.ArgumentParser(description="Pilot monitor for replacement outcome learning")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="storage/pilot/pilot_samples.jsonl")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    parser.add_argument("--samples", type=int, default=24)
    args = parser.parse_args()

    run_loop(
        base_url=args.base_url.rstrip("/"),
        output=Path(args.output),
        interval_seconds=args.interval_seconds,
        samples=args.samples,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
