#!/usr/bin/env python3
"""Check 3-consecutive-run promotion readiness for Phase 1 hardening.

Default behavior is strict:
- each of the last N runs must have verdict PASS
- each run must include posture/compressed/wallclock check artifacts with pass=true
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RunCheckResult:
    run_id: str
    phase1_pass: bool
    posture_tests_pass: bool
    compressed_soak_pass: bool
    wallclock_soak_pass: bool
    streaming_bootstrap_pass: bool
    replay_validation_pass: bool
    websocket_sequence_pass: bool
    errors: list[str]


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _bool_pass(data: dict[str, Any] | None) -> bool:
    if not isinstance(data, dict):
        return False
    if "pass" in data:
        return bool(data.get("pass"))
    if "status" in data:
        return str(data.get("status", "")).upper() == "PASS"
    return False


def _check_one(run_dir: Path, require_streaming_gates: bool) -> RunCheckResult:
    run_id = run_dir.name
    errors: list[str] = []

    verdict = _load_json(run_dir / "verdict.json") or _load_json(run_dir / "run_verdict.json")
    phase1_pass = False
    if verdict is None:
        errors.append("missing or malformed verdict.json")
    else:
        phase1_pass = verdict.get("release_gate_verdict") == "PASS"
        if not phase1_pass:
            errors.append(f"release_gate_verdict={verdict.get('release_gate_verdict')}")

    posture = _load_json(run_dir / "posture_tests.json")
    compressed = _load_json(run_dir / "compressed_soak.json")
    wallclock = _load_json(run_dir / "wallclock_soak.json")
    streaming_bootstrap = _load_json(run_dir / "streaming_bootstrap.json")
    replay_validation = _load_json(run_dir / "replay_validation.json")
    websocket_sequence = _load_json(run_dir / "websocket_sequence.json")

    posture_pass = _bool_pass(posture)
    compressed_pass = _bool_pass(compressed)
    wallclock_pass = _bool_pass(wallclock)
    streaming_bootstrap_pass = _bool_pass(streaming_bootstrap)
    replay_validation_pass = _bool_pass(replay_validation)
    websocket_sequence_pass = _bool_pass(websocket_sequence)

    if not posture_pass:
        errors.append("posture_tests.json missing/fail")
    if not compressed_pass:
        errors.append("compressed_soak.json missing/fail")
    if not wallclock_pass:
        errors.append("wallclock_soak.json missing/fail")

    if require_streaming_gates:
        if not streaming_bootstrap_pass:
            errors.append("streaming_bootstrap.json missing/fail")
        if not replay_validation_pass:
            errors.append("replay_validation.json missing/fail")
        if not websocket_sequence_pass:
            errors.append("websocket_sequence.json missing/fail")

    return RunCheckResult(
        run_id=run_id,
        phase1_pass=phase1_pass,
        posture_tests_pass=posture_pass,
        compressed_soak_pass=compressed_pass,
        wallclock_soak_pass=wallclock_pass,
        streaming_bootstrap_pass=streaming_bootstrap_pass,
        replay_validation_pass=replay_validation_pass,
        websocket_sequence_pass=websocket_sequence_pass,
        errors=errors,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check promotion readiness from consecutive PASS runs")
    parser.add_argument("--artifacts-root", type=Path, default=Path("artifacts/phase1"))
    parser.add_argument("--required-consecutive", type=int, default=3)
    parser.add_argument(
        "--require-streaming-gates",
        action="store_true",
        help="Require streaming_bootstrap.json, replay_validation.json, and websocket_sequence.json to pass",
    )
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if not args.artifacts_root.exists():
        result = {
            "promotion_ready": False,
            "reason": f"artifacts root not found: {args.artifacts_root}",
            "required_consecutive": args.required_consecutive,
            "evaluated_runs": [],
        }
        print(json.dumps(result, indent=2))
        return 2

    run_dirs = sorted([p for p in args.artifacts_root.iterdir() if p.is_dir()])
    selected = run_dirs[-args.required_consecutive :]

    checked = [_check_one(r, require_streaming_gates=args.require_streaming_gates) for r in selected]
    promotion_ready = len(checked) == args.required_consecutive and all(len(c.errors) == 0 for c in checked)

    payload = {
        "promotion_ready": promotion_ready,
        "required_consecutive": args.required_consecutive,
        "require_streaming_gates": args.require_streaming_gates,
        "evaluated_runs": [
            {
                "run_id": c.run_id,
                "phase1_pass": c.phase1_pass,
                "posture_tests_pass": c.posture_tests_pass,
                "compressed_soak_pass": c.compressed_soak_pass,
                "wallclock_soak_pass": c.wallclock_soak_pass,
                "streaming_bootstrap_pass": c.streaming_bootstrap_pass,
                "replay_validation_pass": c.replay_validation_pass,
                "websocket_sequence_pass": c.websocket_sequence_pass,
                "errors": c.errors,
            }
            for c in checked
        ],
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0 if promotion_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
