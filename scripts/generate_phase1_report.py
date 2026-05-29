#!/usr/bin/env python3
"""Generate a consolidated Phase 1 markdown report from run artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _scenario_rows(candidate_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(candidate_dir.glob("*/result.json")):
        scenario = p.parent.name
        data = _load_json(p) or {}
        rows.append(
            {
                "scenario": scenario,
                "pass": bool(data.get("pass", False)),
                "max_band": data.get("max_band", "unknown"),
                "cooldown_converged": bool(data.get("cooldown_converged", False)),
                "telemetry_loss": int(data.get("telemetry_loss", 0) or 0),
                "reconnect_failures": int(data.get("reconnect_failures", 0) or 0),
                "reason": data.get("reason", ""),
            }
        )
    return rows


def generate_report(run_dir: Path, out: Path) -> dict[str, Any]:
    baseline_dir = run_dir / "baseline"
    candidate_dir = run_dir / "candidate"
    comparison_json = run_dir / "comparison.json"

    comparison = _load_json(comparison_json) or {}
    rows = _scenario_rows(candidate_dir)
    pass_count = sum(1 for r in rows if r["pass"])

    lines: list[str] = []
    lines.append("# Phase 1 Runtime Hardening Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Run directory: {run_dir}")
    lines.append("")

    lines.append("## Gate Verdict")
    lines.append("")
    verdict = comparison.get("release_gate_verdict", "UNKNOWN")
    lines.append(f"- Release gate verdict: {verdict}")
    lines.append(f"- Candidate scenario pass count: {pass_count}/{len(rows)}")
    lines.append(f"- Drift failures: {comparison.get('drift_failures', 'n/a')}")
    lines.append(f"- Drift warnings: {comparison.get('drift_warnings', 'n/a')}")
    lines.append("")

    lines.append("## Candidate Scenario Summary")
    lines.append("")
    lines.append("| Scenario | Pass | Max Band | Cooldown Converged | Telemetry Loss | Reconnect Failures | Reason |")
    lines.append("|---|---|---|---|---:|---:|---|")
    for r in rows:
        lines.append(
            f"| {r['scenario']} | {'YES' if r['pass'] else 'NO'} | {r['max_band']} | "
            f"{'YES' if r['cooldown_converged'] else 'NO'} | {r['telemetry_loss']} | {r['reconnect_failures']} | {r['reason']} |"
        )
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    lines.append(f"- Baseline artifacts: {baseline_dir}")
    lines.append(f"- Candidate artifacts: {candidate_dir}")
    lines.append(f"- Comparison JSON: {comparison_json}")
    lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")

    return {
        "report": str(out),
        "release_gate_verdict": verdict,
        "candidate_pass_count": pass_count,
        "candidate_total": len(rows),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Phase 1 consolidated report")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    summary = generate_report(args.run_dir, args.out)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
