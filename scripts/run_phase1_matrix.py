#!/usr/bin/env python3
"""Execute Phase 1 runtime matrix and emit a release gate verdict.

Usage:
  python scripts/run_phase1_matrix.py --simulate
  python scripts/run_phase1_matrix.py --scenario-cmd-template "python -m andie.audio.streaming.realtime_posture_v2 --scenario {scenario} --duration-s {duration_s} --artifact-dir {artifact_dir}"
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

SCENARIOS = [
    ("idle_voice_session", 2 * 60 * 60),
    ("normal_conversation", 2 * 60 * 60),
    ("interrupt_storm", 30 * 60),
    ("recovery_cycle", 30 * 60),
    ("reconnect_storm", 60 * 60),
    ("memory_pressure_run", 2 * 60 * 60),
]


@dataclass
class RunConfig:
    run_id: str
    baseline_ref: str
    output_root: Path
    scenario_cmd_template: str | None
    posture_test_cmd: str | None
    compressed_soak_cmd: str | None
    wallclock_soak_cmd: str | None
    streaming_bootstrap_cmd: str | None
    replay_validation_cmd: str | None
    websocket_sequence_cmd: str | None
    simulate: bool
    strict: bool


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run_cmd(command: str, cwd: Path, stdout_path: Path, stderr_path: Path) -> int:
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )
    stdout_path.write_text(proc.stdout or "", encoding="utf-8")
    stderr_path.write_text(proc.stderr or "", encoding="utf-8")
    return int(proc.returncode)


def _run_gate_check(
    name: str,
    command: str | None,
    cwd: Path,
    run_root: Path,
    strict: bool,
    simulate: bool,
) -> dict[str, Any]:
    out = {
        "name": name,
        "pass": False,
        "command": command,
        "exit_code": None,
        "simulated": simulate,
        "strict_required": strict,
        "reason": "",
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }

    stdout_path = run_root / f"{name}.stdout.log"
    stderr_path = run_root / f"{name}.stderr.log"

    if simulate and not command:
        out["pass"] = True
        out["reason"] = "simulated pass (no command configured)"
    elif not command:
        out["reason"] = "no command configured"
    else:
        rendered = (
            command.replace("{run_root}", str(run_root)).replace("{artifacts_root}", str(run_root))
        )
        out["command"] = rendered
        exit_code = _run_cmd(rendered, cwd, stdout_path, stderr_path)
        out["exit_code"] = exit_code
        out["pass"] = exit_code == 0
        out["reason"] = "command passed" if out["pass"] else f"command failed with exit code {exit_code}"

    json_path = run_root / f"{name}.json"
    json_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def _simulate_telemetry(scenario: str, duration_s: int, out_path: Path) -> None:
    rng = random.Random(f"{scenario}:{duration_s}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    band_profile: list[str]
    if scenario in {"idle_voice_session", "normal_conversation", "memory_pressure_run"}:
        band_profile = ["stable", "warming"]
    elif scenario == "interrupt_storm":
        band_profile = ["warming", "unstable", "critical"]
    elif scenario in {"recovery_cycle", "reconnect_storm"}:
        band_profile = ["warming", "unstable", "warming", "stable"]
    else:
        band_profile = ["stable"]

    lines = []
    now = datetime.now(timezone.utc).timestamp()
    step = max(1, duration_s // 60)
    seq = 1
    cooldown = 120.0

    for i in range(60):
        ts = now + i * step
        band = band_profile[min(i * len(band_profile) // 60, len(band_profile) - 1)]
        turbulence = {
            "stable": 0.0,
            "warming": 0.5,
            "unstable": 1.0,
            "critical": 2.0,
        }[band]

        if scenario in {"recovery_cycle", "normal_conversation", "memory_pressure_run"}:
            cooldown = max(0.0, cooldown - rng.uniform(1.0, 5.0))
        elif scenario == "interrupt_storm":
            cooldown = min(300.0, cooldown + rng.uniform(0.0, 3.0))
        elif scenario == "idle_voice_session":
            cooldown = max(0.0, cooldown - rng.uniform(0.2, 1.2))
        elif scenario == "reconnect_storm":
            cooldown = max(0.0, cooldown - rng.uniform(0.5, 2.0))

        event = {
            "seq": seq,
            "timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
            "posture_band": band,
            "turbulence_severity": turbulence,
            "cooldown_remaining_s": cooldown,
        }

        if scenario == "reconnect_storm" and i % 17 == 0:
            event["event_type"] = "reconnect_success"
        elif scenario == "interrupt_storm" and i % 10 == 0:
            event["event_type"] = "alert"
            event["alert"] = True

        lines.append(json.dumps(event))
        seq += 1

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _collect_result(run_id: str, baseline: str, scenario: str, duration_s: int, telemetry: Path, out_json: Path) -> dict[str, Any]:
    script = REPO_ROOT / "scripts" / "collect_governance_metrics.py"
    cmd = [
        sys.executable,
        str(script),
        "--run-id",
        run_id,
        "--baseline",
        baseline,
        "--scenario",
        scenario,
        "--duration-s",
        str(duration_s),
        "--telemetry-file",
        str(telemetry),
        "--out",
        str(out_json),
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"metric collection failed for {scenario}: {proc.stderr}")
    return json.loads(out_json.read_text(encoding="utf-8"))


def _run_matrix_for_target(
    target_name: str,
    workdir: Path,
    cfg: RunConfig,
) -> dict[str, Any]:
    target_dir = cfg.output_root / cfg.run_id / target_name
    target_dir.mkdir(parents=True, exist_ok=True)

    scenario_results: list[dict[str, Any]] = []

    for scenario, duration_s in SCENARIOS:
        scenario_dir = target_dir / scenario
        scenario_dir.mkdir(parents=True, exist_ok=True)

        telemetry_file = scenario_dir / "telemetry.ndjson"
        stdout_file = scenario_dir / "stdout.log"
        stderr_file = scenario_dir / "stderr.log"

        if cfg.simulate:
            _simulate_telemetry(scenario, duration_s, telemetry_file)
            exit_code = 0
            command = "SIMULATED"
        else:
            if not cfg.scenario_cmd_template:
                exit_code = 2
                command = "UNCONFIGURED"
                stderr_file.write_text(
                    "No scenario command template provided. Use --scenario-cmd-template or --simulate.\n",
                    encoding="utf-8",
                )
                stdout_file.write_text("", encoding="utf-8")
            else:
                command = cfg.scenario_cmd_template.format(
                    scenario=scenario,
                    duration_s=duration_s,
                    artifact_dir=str(scenario_dir),
                    run_role=target_name,
                )
                exit_code = _run_cmd(command, workdir, stdout_file, stderr_file)

        result_json = scenario_dir / "result.json"
        result = _collect_result(
            run_id=cfg.run_id,
            baseline=cfg.baseline_ref,
            scenario=scenario,
            duration_s=duration_s,
            telemetry=telemetry_file,
            out_json=result_json,
        )
        result["command"] = command
        result["command_exit_code"] = exit_code
        if exit_code != 0:
            result["pass"] = False
            reason = result.get("reason", "")
            result["reason"] = (reason + "; " if reason else "") + f"scenario command failed with exit code {exit_code}"
            result_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

        scenario_results.append(result)

    summary = {
        "run_id": cfg.run_id,
        "target": target_name,
        "scenario_count": len(scenario_results),
        "pass_count": sum(1 for r in scenario_results if r.get("pass")),
        "results": scenario_results,
    }
    (target_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _validate_result_shape(result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_top = [
        "run_id",
        "baseline",
        "scenario",
        "duration_s",
        "max_band",
        "cooldown_converged",
        "telemetry_loss",
        "reconnect_failures",
        "pass",
        "metrics",
    ]
    for key in required_top:
        if key not in result:
            errors.append(f"missing key: {key}")

    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics must be an object")
        return errors

    required_metrics = [
        "escalation_latency_s",
        "cooldown_duration_s",
        "instability_peak",
        "alert_rate_per_min",
        "recovery_convergence_time_s",
    ]
    for key in required_metrics:
        if key not in metrics:
            errors.append(f"missing metric: {key}")

    return errors


def _strict_validate_target_artifacts(target_dir: Path) -> list[str]:
    errors: list[str] = []
    for scenario, _duration_s in SCENARIOS:
        scenario_dir = target_dir / scenario
        telemetry = scenario_dir / "telemetry.ndjson"
        result_path = scenario_dir / "result.json"

        if not telemetry.exists():
            errors.append(f"{scenario}: missing telemetry.ndjson")
        if not result_path.exists():
            errors.append(f"{scenario}: missing result.json")
            continue

        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append(f"{scenario}: malformed result.json")
            continue

        shape_errors = _validate_result_shape(result)
        for err in shape_errors:
            errors.append(f"{scenario}: {err}")

        if not result.get("telemetry_present", False):
            errors.append(f"{scenario}: telemetry_present=false")

        metrics = result.get("metrics") or {}
        for m_key in [
            "escalation_latency_s",
            "cooldown_duration_s",
            "instability_peak",
            "alert_rate_per_min",
            "recovery_convergence_time_s",
        ]:
            if metrics.get(m_key) is None:
                errors.append(f"{scenario}: metric {m_key}=null")

    return errors


def _ensure_baseline_worktree(baseline_ref: str, run_id: str) -> tuple[Path, bool]:
    worktree_root = REPO_ROOT / ".phase1_worktrees"
    worktree_root.mkdir(parents=True, exist_ok=True)
    baseline_path = worktree_root / f"{run_id}_baseline"

    if baseline_path.exists():
        shutil.rmtree(baseline_path)

    cmd = ["git", "worktree", "add", "--detach", str(baseline_path), baseline_ref]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return (REPO_ROOT, False)
    return (baseline_path, True)


def _remove_baseline_worktree(path: Path) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=str(REPO_ROOT), check=False)


def _compare_and_report(run_root: Path, strict: bool) -> tuple[dict[str, Any], Path, Path]:
    cmp_md = run_root / "comparison.md"
    cmp_json = run_root / "comparison.json"
    report_md = run_root / "report.md"

    compare_script = REPO_ROOT / "scripts" / "compare_phase1_runs.py"
    report_script = REPO_ROOT / "scripts" / "generate_phase1_report.py"

    subprocess.run(
        [
            sys.executable,
            str(compare_script),
            "--baseline",
            str(run_root / "baseline"),
            "--candidate",
            str(run_root / "candidate"),
            "--out",
            str(cmp_md),
            "--json-out",
            str(cmp_json),
            *( ["--strict-metrics"] if strict else [] ),
        ],
        cwd=str(REPO_ROOT),
        check=False,
    )

    subprocess.run(
        [
            sys.executable,
            str(report_script),
            "--run-dir",
            str(run_root),
            "--out",
            str(report_md),
        ],
        cwd=str(REPO_ROOT),
        check=False,
    )

    comparison = {}
    if cmp_json.exists():
        comparison = json.loads(cmp_json.read_text(encoding="utf-8"))

    return (comparison, cmp_md, report_md)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 1 hardening matrix and produce a release verdict")
    parser.add_argument("--baseline-ref", default="posture-governance-baseline-v1")
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "artifacts" / "phase1")
    parser.add_argument(
        "--scenario-cmd-template",
        default=os.environ.get("PHASE1_SCENARIO_CMD_TEMPLATE"),
        help="Template command with placeholders: {scenario}, {duration_s}, {artifact_dir}, {run_role}",
    )
    parser.add_argument(
        "--posture-test-cmd",
        default=os.environ.get("PHASE1_POSTURE_TEST_CMD"),
        help="Shell command that validates posture tests and returns 0 on pass",
    )
    parser.add_argument(
        "--compressed-soak-cmd",
        default=os.environ.get("PHASE1_COMPRESSED_SOAK_CMD"),
        help="Shell command that validates compressed soak and returns 0 on pass",
    )
    parser.add_argument(
        "--wallclock-soak-cmd",
        default=os.environ.get("PHASE1_WALLCLOCK_SOAK_CMD"),
        help="Shell command that validates wall-clock soak and returns 0 on pass",
    )
    parser.add_argument(
        "--streaming-bootstrap-cmd",
        default=os.environ.get("PHASE1_STREAMING_BOOTSTRAP_CMD"),
        help="Shell command that validates websocket bootstrap contract and returns 0 on pass",
    )
    parser.add_argument(
        "--replay-validation-cmd",
        default=os.environ.get("PHASE1_REPLAY_VALIDATION_CMD"),
        help="Shell command that validates replay drilldown behavior and returns 0 on pass",
    )
    parser.add_argument(
        "--websocket-sequence-cmd",
        default=os.environ.get("PHASE1_WEBSOCKET_SEQUENCE_CMD"),
        help="Shell command that validates websocket sequencing behavior and returns 0 on pass",
    )
    parser.add_argument("--simulate", action="store_true", help="Generate synthetic telemetry to validate pipeline wiring")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on missing/malformed artifacts or metrics and enforce strict comparison behavior",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run_id = _utc_stamp()

    cfg = RunConfig(
        run_id=run_id,
        baseline_ref=args.baseline_ref,
        output_root=args.output_root,
        scenario_cmd_template=args.scenario_cmd_template,
        posture_test_cmd=args.posture_test_cmd,
        compressed_soak_cmd=args.compressed_soak_cmd,
        wallclock_soak_cmd=args.wallclock_soak_cmd,
        streaming_bootstrap_cmd=args.streaming_bootstrap_cmd,
        replay_validation_cmd=args.replay_validation_cmd,
        websocket_sequence_cmd=args.websocket_sequence_cmd,
        simulate=args.simulate,
        strict=args.strict,
    )

    run_root = cfg.output_root / cfg.run_id
    run_root.mkdir(parents=True, exist_ok=True)

    candidate_summary = _run_matrix_for_target("candidate", REPO_ROOT, cfg)

    baseline_workdir, baseline_ok = _ensure_baseline_worktree(cfg.baseline_ref, cfg.run_id)
    try:
        baseline_summary = _run_matrix_for_target("baseline", baseline_workdir, cfg)
    finally:
        if baseline_ok and baseline_workdir != REPO_ROOT:
            _remove_baseline_worktree(baseline_workdir)

    strict_errors: list[str] = []
    if cfg.strict:
        strict_errors.extend(_strict_validate_target_artifacts(run_root / "candidate"))
        strict_errors.extend(_strict_validate_target_artifacts(run_root / "baseline"))

    comparison, comparison_md, report_md = _compare_and_report(run_root, strict=cfg.strict)

    verdict = comparison.get("release_gate_verdict", "FAIL")
    if cfg.strict and not comparison:
        strict_errors.append("comparison unavailable")
    if cfg.strict and strict_errors:
        verdict = "FAIL"

    baseline_snapshot = {
        "target": "baseline",
        "summary": baseline_summary,
    }
    candidate_snapshot = {
        "target": "candidate",
        "summary": candidate_summary,
    }
    delta_snapshot = comparison if comparison else {"error": "comparison unavailable"}

    posture_gate = _run_gate_check(
        name="posture_tests",
        command=cfg.posture_test_cmd,
        cwd=REPO_ROOT,
        run_root=run_root,
        strict=cfg.strict,
        simulate=cfg.simulate,
    )
    compressed_gate = _run_gate_check(
        name="compressed_soak",
        command=cfg.compressed_soak_cmd,
        cwd=REPO_ROOT,
        run_root=run_root,
        strict=cfg.strict,
        simulate=cfg.simulate,
    )
    wallclock_gate = _run_gate_check(
        name="wallclock_soak",
        command=cfg.wallclock_soak_cmd,
        cwd=REPO_ROOT,
        run_root=run_root,
        strict=cfg.strict,
        simulate=cfg.simulate,
    )
    streaming_bootstrap_gate = _run_gate_check(
        name="streaming_bootstrap",
        command=cfg.streaming_bootstrap_cmd,
        cwd=REPO_ROOT,
        run_root=run_root,
        strict=cfg.strict,
        simulate=cfg.simulate,
    )
    replay_validation_gate = _run_gate_check(
        name="replay_validation",
        command=cfg.replay_validation_cmd,
        cwd=REPO_ROOT,
        run_root=run_root,
        strict=cfg.strict,
        simulate=cfg.simulate,
    )
    websocket_sequence_gate = _run_gate_check(
        name="websocket_sequence",
        command=cfg.websocket_sequence_cmd,
        cwd=REPO_ROOT,
        run_root=run_root,
        strict=cfg.strict,
        simulate=cfg.simulate,
    )

    if cfg.strict:
        for gate in (
            posture_gate,
            compressed_gate,
            wallclock_gate,
            streaming_bootstrap_gate,
            replay_validation_gate,
            websocket_sequence_gate,
        ):
            if not gate.get("pass", False):
                strict_errors.append(f"{gate['name']} failed: {gate.get('reason', 'unknown reason')}")

    out = {
        "run_id": cfg.run_id,
        "baseline_ref": cfg.baseline_ref,
        "candidate_pass_count": candidate_summary["pass_count"],
        "candidate_total": candidate_summary["scenario_count"],
        "baseline_pass_count": baseline_summary["pass_count"],
        "baseline_total": baseline_summary["scenario_count"],
        "release_gate_verdict": verdict,
        "strict": cfg.strict,
        "strict_errors": strict_errors,
        "gate_checks": {
            "posture_tests": posture_gate,
            "compressed_soak": compressed_gate,
            "wallclock_soak": wallclock_gate,
            "streaming_bootstrap": streaming_bootstrap_gate,
            "replay_validation": replay_validation_gate,
            "websocket_sequence": websocket_sequence_gate,
        },
        "comparison_markdown": str(comparison_md),
        "report_markdown": str(report_md),
        "artifacts_root": str(run_root),
    }

    (run_root / "baseline.json").write_text(json.dumps(baseline_snapshot, indent=2), encoding="utf-8")
    (run_root / "candidate.json").write_text(json.dumps(candidate_snapshot, indent=2), encoding="utf-8")
    (run_root / "delta.json").write_text(json.dumps(delta_snapshot, indent=2), encoding="utf-8")
    (run_root / "verdict.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    (run_root / "run_verdict.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))

    if cfg.strict:
        return 0 if verdict == "PASS" else 2
    return 0 if verdict in {"PASS", "PASS_WITH_WARNINGS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
