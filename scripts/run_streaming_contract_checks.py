#!/usr/bin/env python3
"""Run streaming contract checks and emit JSON gate artifacts.

Supported checks:
- bootstrap: connection.ready -> workspace.snapshot
- replay: replay drilldown behavior
- sequence: alias normalization / sequencing behavior
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _load_test_module(test_file: Path):
    spec = importlib.util.spec_from_file_location("streaming_tests", test_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load test module from {test_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_one(check: str, mod: Any) -> tuple[bool, str]:
    mapping: dict[str, Callable[[], None]] = {
        "bootstrap": getattr(mod, "test_connection_ready_then_workspace_snapshot_sequence"),
        "replay": getattr(mod, "test_replay_drilldown_returns_execution_events"),
        "sequence": getattr(mod, "test_alias_route_normalization_matches_canonical_bootstrap"),
    }

    fn = mapping.get(check)
    if fn is None:
        return (False, f"unsupported check: {check}")

    try:
        fn()
        return (True, "check passed")
    except Exception as exc:  # pragma: no cover - runtime assertion path
        return (False, f"check failed: {exc}")


def _artifact_name(check: str) -> str:
    return {
        "bootstrap": "streaming_bootstrap.json",
        "replay": "replay_validation.json",
        "sequence": "websocket_sequence.json",
    }[check]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run streaming contract checks and emit gate JSON")
    parser.add_argument("--check", choices=["bootstrap", "replay", "sequence"], required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--test-file", type=Path, default=Path("tests/test_streaming_bootstrap.py"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    mod = _load_test_module(args.test_file)
    passed, reason = _run_one(args.check, mod)

    payload = {
        "check": args.check,
        "pass": passed,
        "status": "PASS" if passed else "FAIL",
        "reason": reason,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    out_path = args.out or Path(".") / _artifact_name(args.check)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
