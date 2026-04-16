from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Dict, List

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autonomy.memory_store import ALLOW_BACKFILL
from interfaces.api.outcome_tracking import record_skill_outcome_internal


REPLACEMENT_MAP: Dict[str, List[str]] = {
    "check_service_status": ["restart_encoder", "switch_stream"],
    "restart_server": ["check_service_status", "restart_encoder"],
}

BASE_SUCCESS_RATES: Dict[str, float] = {
    "restart_encoder": 0.75,
    "switch_stream": 0.60,
    "check_service_status": 0.70,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed replacement outcomes for evidence acceleration")
    parser.add_argument("--context", default="hls_stream")
    parser.add_argument("--samples-per-pair", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--direct-memory", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def simulate_outcome(rng: random.Random, skill: str) -> str:
    rate = max(0.0, min(1.0, BASE_SUCCESS_RATES.get(skill, 0.65)))
    return "success" if rng.random() < rate else "failure"


def run() -> int:
    args = parse_args()
    if not ALLOW_BACKFILL and not args.dry_run:
        raise RuntimeError("Backfill disabled in this environment. Set ANDIE_ALLOW_BACKFILL=true to enable.")

    rng = random.Random(args.seed)
    samples = max(1, int(args.samples_per_pair))

    totals = {"success": 0, "failure": 0, "events": 0}
    for replaced_from, skills in REPLACEMENT_MAP.items():
        for skill in skills:
            for _ in range(samples):
                result = simulate_outcome(rng, skill)
                totals[result] += 1
                totals["events"] += 1

                if args.dry_run:
                    continue

                if args.direct_memory:
                    record_skill_outcome_internal(
                        skill_name=skill,
                        result=result,
                        context_key=args.context,
                        replaced_from=replaced_from,
                        latency=0.08,
                        error="synthetic_backfill" if result == "failure" else None,
                        record_execution=True,
                        source="synthetic",
                    )
                else:
                    payload = {
                        "skill": skill,
                        "result": result,
                        "context_key": args.context,
                        "replaced_from": replaced_from,
                        "latency": 0.08,
                        "error": "synthetic_backfill" if result == "failure" else None,
                        "record_execution": True,
                        "source": "synthetic",
                    }
                    response = requests.post(
                        f"{args.base_url.rstrip('/')}/skills/outcome",
                        json=payload,
                        timeout=max(1, int(args.timeout)),
                    )
                    response.raise_for_status()

    if args.dry_run:
        mode = "dry_run"
    elif args.direct_memory:
        mode = "applied_direct_memory"
    else:
        mode = "applied_via_api"

    print(
        "backfill_outcomes "
        f"mode={mode} "
        f"context={args.context} "
        f"samples_per_pair={samples} "
        f"events={totals['events']} "
        f"success={totals['success']} "
        f"failure={totals['failure']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
