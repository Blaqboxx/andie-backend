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


def fetch_dashboard(base_url: str, timeout: int) -> Dict[str, Any]:
    response = requests.get(f"{base_url.rstrip('/')}/trust/dashboard", timeout=max(1, timeout))
    response.raise_for_status()
    payload = response.json() or {}
    return {
        "timestamp": now_iso(),
        "confidence_tier": payload.get("confidence_tier"),
        "decision": payload.get("decision"),
        "real_vs_synthetic": payload.get("real_vs_synthetic") or {},
        "learning_velocity": payload.get("learning_velocity") or {},
    }


def append_jsonl(path: Path, item: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")


def run_loop(base_url: str, output: Path, interval_seconds: int, timeout: int, max_samples: int | None) -> int:
    last_tier: str | None = None
    last_decision: str | None = None
    count = 0

    while True:
        item = fetch_dashboard(base_url, timeout)
        tier = str(item.get("confidence_tier") or "unknown")
        decision = str(item.get("decision") or "unknown")

        changed = (tier != last_tier) or (decision != last_decision)
        if changed:
            event = {
                "event": "tier_or_decision_change",
                **item,
                "previous": {
                    "confidence_tier": last_tier,
                    "decision": last_decision,
                },
            }
            append_jsonl(output, event)
            print(json.dumps(event, sort_keys=True))
            last_tier = tier
            last_decision = decision

        count += 1
        if max_samples is not None and count >= max_samples:
            return 0

        time.sleep(max(1, interval_seconds))


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuously watch trust tier and report changes")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="storage/pilot/tier_changes.jsonl")
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--samples", type=int, default=0, help="0 = run forever")
    args = parser.parse_args()

    max_samples = None if int(args.samples) <= 0 else int(args.samples)
    return run_loop(
        base_url=args.base_url,
        output=Path(args.output),
        interval_seconds=int(args.interval_seconds),
        timeout=int(args.timeout),
        max_samples=max_samples,
    )


if __name__ == "__main__":
    raise SystemExit(main())
