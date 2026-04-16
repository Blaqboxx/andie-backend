# Pilot Window (3-7 Days)

This pilot validates replacement outcome learning in day-to-day traffic.

## Preflight

1. Confirm API health and control-plane metrics endpoint:
   - GET /health
   - GET /metrics/control-plane
2. Confirm runtime guardrails are enabled:
   - outcome_weighting_enabled = true
   - runtime_outcome_emission_enabled = true
   - observability_alerts_enabled = true
3. Confirm alert log path is writable:
   - logs/observability-alerts.log

## Run Plan

1. Start hourly monitor sampling:
   - /home/jamai-jamison/valhalla/.venv/bin/python tools/pilot_monitor.py --base-url http://127.0.0.1:8000 --interval-seconds 3600 --samples 168
2. Keep normal assisted-mode operations active.
3. Review these indicators daily:
   - replacement_success_rate_pct
   - learning_signal_density
   - drift_intensity
   - alert counters (ingestion failures, drift spikes, memory write errors)
4. Run the automated daily gate (fails non-zero on violations):
   - /home/jamai-jamison/valhalla/.venv/bin/python tools/pilot_daily_check.py --base-url http://127.0.0.1:8000 --samples-file storage/pilot/pilot_samples.jsonl --lookback-hours 24
5. Use the rollout checklist for PR evidence and go/no-go decisions:
   - PILOT_ROLLOUT_CHECKLIST.md
6. If evidence is sparse, backfill replacement outcomes before final decision:
   - export ANDIE_ALLOW_BACKFILL=true
   - /home/jamai-jamison/valhalla/.venv/bin/python scripts/backfill_outcomes.py --context hls_stream --samples-per-pair 40

## Optional Soak Test

Run concurrent outcome write soak in a non-production environment:

- ANDIE_RUN_SOAK=1 /home/jamai-jamison/valhalla/.venv/bin/python -m pytest tests/test_outcome_soak.py -q

## Rollback Procedure

If drift spikes or ingestion failures increase materially, disable the risky path quickly:

1. POST /autonomy/config with:
   - {"outcome_weighting_enabled": false}
2. POST /autonomy/config with:
   - {"runtime_outcome_emission_enabled": false}
3. Keep alerts enabled for diagnosis:
   - {"observability_alerts_enabled": true}

## Exit Criteria

A pilot is considered successful when:

- sample_size >= 20 in the 24h gate window
- learning_signal_density is stable above minimum threshold
- replacement_success_rate_pct is stable or improving
- drift_intensity is stable/mild for most samples
- alert_outcome_ingestion_failures stays at 0
- alert_memory_write_errors stays at 0
- score_drift_spike alerts remain low and explainable
