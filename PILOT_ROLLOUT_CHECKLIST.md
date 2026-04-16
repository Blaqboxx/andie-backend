# Pilot Rollout Checklist (PR-Ready)

Use this checklist to run a controlled 3-7 day rollout with explicit go/no-go criteria.

## Scope

- Environment: assisted-mode operations
- Duration: 3-7 days
- Sampling cadence: every 15 minutes (recommended)

## Pre-Launch Checks

- [ ] `/health` returns HTTP 200
- [ ] `/metrics/control-plane` returns HTTP 200
- [ ] `/autonomy/drift` returns HTTP 200
- [ ] Guardrails set:
  - [ ] `outcome_weighting_enabled = true`
  - [ ] `runtime_outcome_emission_enabled = true`
  - [ ] `observability_alerts_enabled = true`
- [ ] Alert log path writable: `logs/observability-alerts.log`

## Start Monitoring

Use 15-minute sampling for a 7-day window (672 samples):

```bash
/home/jamai-jamison/valhalla/.venv/bin/python tools/pilot_monitor.py \
  --base-url http://127.0.0.1:8000 \
  --interval-seconds 900 \
  --samples 672 \
  --output storage/pilot/pilot_samples.jsonl
```

## Daily Automated Gate

Run once per day (or in CI/cron) to enforce pilot guardrails:

```bash
/home/jamai-jamison/valhalla/.venv/bin/python tools/pilot_daily_check.py \
  --base-url http://127.0.0.1:8000 \
  --samples-file storage/pilot/pilot_samples.jsonl \
  --lookback-hours 24
```

Expected exit codes:

- `0`: pass
- `2`: one or more criteria failed

Per-run JSON output is archived automatically when using:

```bash
/bin/bash tools/run_pilot_daily_gate.sh
```

Archive location:

- `storage/pilot/daily_checks/<timestamp>.json`

## Scheduler Setup (Optional)

Install cron job (default: 07:05 UTC daily):

```bash
/bin/bash tools/install_pilot_daily_cron.sh
```

Install systemd user timer (default: 07:05 UTC daily):

```bash
/bin/bash tools/install_pilot_daily_systemd.sh
```

## Go/No-Go Thresholds

Daily gate defaults (override with script flags when needed):

- Sample size in lookback window: `>= 20`
- Learning signal density (outcome events/hour): `>= 5.0`
- Replacement success rate (avg, 24h): `>= 70.0%`
- Drift rate: `<= 0.15`
- Severe drift ratio (24h): `<= 10%`
- Score drift spike alerts (counter): `<= 2`
- Outcome ingestion failure alerts (counter): `== 0`
- Memory write error alerts (counter): `== 0`

Interpretation:

- Go: all checks pass for at least 2 consecutive daily runs.
- No-Go: any check fails, or trend degrades for 2 consecutive days.

## Rollback Switches

Fast disable learning influence while preserving observability:

```bash
curl -sS -X POST http://127.0.0.1:8000/autonomy/config \
  -H "Content-Type: application/json" \
  -d '{"outcome_weighting_enabled": false, "runtime_outcome_emission_enabled": false, "observability_alerts_enabled": true}'
```

## Evidence to Attach in PR

- [ ] Last 2 daily `pilot_daily_check.py` outputs (JSON)
- [ ] Sample tail from `storage/pilot/pilot_samples.jsonl`
- [ ] Current counters from `/metrics/control-plane`
- [ ] Any rollback events and rationale (if applicable)
- [ ] Final decision: Go or No-Go with timestamp and approver

Generate a one-line PR-ready decision summary:

```bash
/home/jamai-jamison/valhalla/.venv/bin/python tools/pilot_decision_summary.py
```

## Evidence Acceleration (Backfill)

Use controlled synthetic/replay-style outcomes to increase evidence density before final GO evaluation:

```bash
export ANDIE_ALLOW_BACKFILL=true
/home/jamai-jamison/valhalla/.venv/bin/python scripts/backfill_outcomes.py \
  --context hls_stream \
  --samples-per-pair 40
```

Recommended target before final decision:

- `30-50` outcomes per key replacement path
