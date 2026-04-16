from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class RunRecord:
    timestamp: str
    passed: bool
    checks: List[Dict[str, Any]]
    path: Path


def _load_run(path: Path) -> RunRecord | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    return RunRecord(
        timestamp=path.stem,
        passed=bool(payload.get("passed", False)),
        checks=checks,
        path=path,
    )


def load_recent_runs(directory: Path, count: int) -> List[RunRecord]:
    if not directory.exists():
        return []

    paths = sorted(directory.glob("*.json"), reverse=True)
    runs: List[RunRecord] = []
    for path in paths:
        record = _load_run(path)
        if record is None:
            continue
        runs.append(record)
        if len(runs) >= max(1, count):
            break
    return runs


def failed_check_names(run: RunRecord) -> List[str]:
    names: List[str] = []
    for check in run.checks:
        if bool(check.get("passed", False)):
            continue
        name = str(check.get("name") or "unknown_check").strip() or "unknown_check"
        names.append(name)
    return names


def summarize(runs: List[RunRecord], required_consecutive_passes: int = 2) -> Dict[str, Any]:
    if not runs:
        return {
            "decision": "NO_GO",
            "reason": "no_daily_check_runs_found",
            "line": "NO_GO - no daily check archives found",
            "exitCode": 3,
        }

    recent = runs[: max(1, required_consecutive_passes)]
    all_recent_pass = len(recent) >= required_consecutive_passes and all(run.passed for run in recent)

    latest = runs[0]
    latest_failures = failed_check_names(latest)
    latest_failures_text = ", ".join(latest_failures) if latest_failures else "none"

    if all_recent_pass:
        line = (
            f"GO - {required_consecutive_passes} consecutive daily gates passed "
            f"(latest={latest.timestamp}, failures={latest_failures_text})"
        )
        return {
            "decision": "GO",
            "reason": "consecutive_pass_threshold_met",
            "line": line,
            "exitCode": 0,
            "latest": latest.path.name,
            "evaluatedRuns": len(recent),
        }

    line = (
        f"NO_GO - gate threshold not met "
        f"(latest={latest.timestamp}, latest_failures={latest_failures_text})"
    )
    return {
        "decision": "NO_GO",
        "reason": "consecutive_pass_threshold_not_met",
        "line": line,
        "exitCode": 2,
        "latest": latest.path.name,
        "evaluatedRuns": len(recent),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize archived pilot daily checks into a GO/NO_GO decision line")
    parser.add_argument("--checks-dir", default="storage/pilot/daily_checks")
    parser.add_argument("--required-consecutive-passes", type=int, default=2)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    checks_dir = Path(args.checks_dir)
    runs = load_recent_runs(checks_dir, count=max(2, args.required_consecutive_passes))
    result = summarize(runs, required_consecutive_passes=max(1, args.required_consecutive_passes))

    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result.get("line", "NO_GO - summary unavailable"))

    return int(result.get("exitCode", 2))


if __name__ == "__main__":
    raise SystemExit(main())
