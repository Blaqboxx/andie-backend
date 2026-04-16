from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import Dict

import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interfaces.api.outcome_tracking import record_skill_outcome_internal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill replacement outcome evidence in batch")
    parser.add_argument("--skill", default="resync_audio")
    parser.add_argument("--replaced-from", default="analyze_video")
    parser.add_argument("--context-key", default="hls")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--success-ratio", type=float, default=0.8)
    parser.add_argument("--latency", type=float, default=0.08)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    total = max(1, int(args.count))
    success_ratio = max(0.0, min(float(args.success_ratio), 1.0))

    rng = random.Random(args.seed)

    summary: Dict[str, int] = {"success": 0, "failure": 0}
    for _ in range(total):
        result = "success" if rng.random() < success_ratio else "failure"
        summary[result] += 1

        if args.dry_run:
            continue

        record_skill_outcome_internal(
            skill_name=args.skill,
            result=result,
            context_key=args.context_key,
            replaced_from=args.replaced_from,
            latency=float(args.latency),
            error="synthetic_backfill" if result == "failure" else None,
            record_execution=True,
        )

    mode = "dry_run" if args.dry_run else "applied"
    print(
        "backfill_outcomes "
        f"mode={mode} "
        f"skill={args.skill} "
        f"replaced_from={args.replaced_from} "
        f"context_key={args.context_key} "
        f"count={total} "
        f"success={summary['success']} "
        f"failure={summary['failure']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())