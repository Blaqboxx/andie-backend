# ANDIE Phase 1 Hardening Plan

## Objective
Move posture-governance from validated feature status to release-gate status by proving runtime behavior over sustained load and perturbation.

## Scope
This phase covers runtime reliability only:
- endurance under continuous operation
- recovery behavior under disruption
- governance stability under pressure
- telemetry integrity across reconnect and churn

It does not include new governance features.

## Baseline Anchor
- Repository: Blaqboxx/andie-backend
- Baseline commit: 5716e77891a65d2225e541ccb984bd71dd6a47a6
- Baseline tag: posture-governance-baseline-v1
- Baseline branch: release/governance-stabilization-2026-05-28

## Phase 1 Test Matrix

| Scenario | Duration | Core Stimulus | Success Criteria |
|---|---:|---|---|
| Idle voice session | 2h | Minimal traffic, keep-alive cadence only | No instability drift; posture remains stable/nominal; no escalation leak |
| Normal conversation | 2h | Typical turn cadence and interruptions | Stable -> warming transitions only when justified by pressure; cooldown returns to stable window |
| Interrupt storm | 30m | Repeated user interruptions and partial utterances | Escalation reaches unstable/critical when thresholds are crossed; no deadlock; no runaway escalation |
| Recovery cycle | 30m | Inject destabilization then remove stimulus | Cooldown converges below turbulence severity within configured recovery window |
| Reconnect storm | 1h | Repeated websocket drops/rejoins | No telemetry corruption or duplicate event explosion; state continuity preserved or safely reset |
| Memory pressure run | 2h | High-context retention and repeated recalls | No governance degradation; latency increase remains bounded; no posture contract violation |

## Release-Gate Criteria (Phase 1)
All criteria must pass in the same run window:
1. 100% scenario pass rate in matrix above.
2. No governance contract failures from posture runtime contracts.
3. No unbounded escalation state persistence after stimulus removal.
4. No telemetry integrity violations (duplicate sequence breaks, malformed envelope, missing required fields).
5. No process crashes, restart loops, or memory runaway beyond defined limits.

## Required Metrics
Collect per scenario:
- transition counts by posture state
- escalation level histogram and dwell time
- cooldown convergence time distribution
- turbulence severity timeline
- reconnect count and successful recovery count
- event loss/duplication indicators
- p50/p95/p99 processing latency
- memory footprint trend (RSS) and growth slope

## Execution Protocol
1. Pin run to baseline tag first for control measurement.
2. Execute full matrix and store artifacts under timestamped run folder.
3. Re-run same matrix on candidate branch.
4. Compare candidate vs baseline on convergence, escalation dwell, latency, and integrity.
5. Fail fast on any contract violation; capture logs and timeline snapshot.

## Command-Ready Runbook

### 1) Prepare environment
```bash
cd /home/jamai-jamison/github/Blaqboxx/andie-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt || true
```

### 2) Run baseline control (tagged checkpoint)
```bash
git checkout posture-governance-baseline-v1
mkdir -p artifacts/phase1/$(date +%F)/baseline
# Replace with repo-specific runner entrypoint if different
python -m andie.audio.streaming.realtime_posture_v2 \
  --scenario-matrix phase1 \
  --artifact-dir artifacts/phase1/$(date +%F)/baseline
```

### 3) Run posture contract/regression tests
```bash
# Narrow to posture runtime tests when present
pytest -q -k "posture or governance or drift or cooldown" || true
```

### 4) Run candidate build and compare
```bash
git checkout <candidate-branch-or-sha>
mkdir -p artifacts/phase1/$(date +%F)/candidate
python -m andie.audio.streaming.realtime_posture_v2 \
  --scenario-matrix phase1 \
  --artifact-dir artifacts/phase1/$(date +%F)/candidate
```

### 5) Diff baseline vs candidate behavior
```bash
python scripts/compare_phase1_runs.py \
  --baseline artifacts/phase1/$(date +%F)/baseline \
  --candidate artifacts/phase1/$(date +%F)/candidate \
  --out artifacts/phase1/$(date +%F)/comparison.md
```

### 6) Strict release gate mode (fail-closed)
```bash
python scripts/run_phase1_matrix.py --strict \
  --scenario-cmd-template "python -m andie.audio.streaming.realtime_posture_v2 --scenario {scenario} --duration-s {duration_s} --artifact-dir {artifact_dir}"
```

Strict mode rules:
- missing metric -> FAIL
- malformed JSON -> FAIL
- missing artifact -> FAIL
- comparison unavailable -> FAIL

### 7) Promotion gate (3 consecutive PASS runs)
```bash
python scripts/check_promotion_gate.py \
  --artifacts-root artifacts/phase1 \
  --required-consecutive 3
```

Each run directory should contain:
- `verdict.json`
- `posture_tests.json`
- `compressed_soak.json`
- `wallclock_soak.json`

## Stop Conditions
Abort run and mark phase failed if any occurs:
- contract assertion failure in posture runtime
- sustained critical escalation after stimulus removal beyond policy window
- telemetry stream corruption or unrecoverable desync
- process crash/restart loop

## Promotion Rule
Promote only if:
- matrix pass rate = 6/6
- contract failures = 0
- cooldown convergence is not worse than baseline by agreed threshold
- no new integrity regressions

## Phase 1 Deliverables
- scenario logs and metric bundles per run
- baseline vs candidate comparison report
- release-gate verdict (PASS/FAIL)
- defect list with reproduction steps for any failure
