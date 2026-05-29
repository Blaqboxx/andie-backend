# Platform Reliability Baseline - 2026-05-29

## Milestone
ANDIE reached promotion-ready status under strict gate enforcement across Governance, Streaming, and Replay.

## Qualification Result
- promotion_ready: true
- required_consecutive: 3
- require_streaming_gates: true

Promotion artifact:
- artifacts/phase1/promotion_gate.json

## Qualifying Run IDs
1. 20260529T002814Z
2. 20260529T003345Z
3. 20260529T003352Z

## Gate Definitions
### Governance Gates
- posture_tests
- compressed_soak
- wallclock_soak

### Streaming Gates
- streaming_bootstrap
- websocket_sequence

### Replay Gates
- replay_validation

## Required Per-Run Artifacts
- baseline.json
- candidate.json
- delta.json
- verdict.json
- comparison.json
- comparison.md
- report.md
- posture_tests.json
- compressed_soak.json
- wallclock_soak.json
- streaming_bootstrap.json
- replay_validation.json
- websocket_sequence.json

## Promotion Criteria (Frozen)
- Three consecutive strict PASS runs.
- Governance + Streaming + Replay gates all PASS in each run.
- No comparison drift failures in strict mode.

## Baseline Commit
- d90761cc4509ad6e7e1f3f0222dd8e5d35020976

## Baseline Tag
- platform-reliability-baseline-v1

## Known Limitations
- Current strict qualification evidence was produced in simulated scenario mode for matrix runtime stimuli.
- Real production qualification should run with live scenario commands and the same strict gate set.

## Phase 2 Entry Criteria
- Promotion gate remains green under frozen criteria.
- No changes to promotion rules until Phase 2A event envelope stabilization is complete.

## Phase 2A Objective
Standardize event envelope across snapshot, stream, and replay paths:

{
  "event_id": "uuid",
  "event_type": "string",
  "timestamp": "iso8601",
  "execution_id": "string",
  "source": "string",
  "payload": {}
}

This envelope is the required contract before expanding event taxonomy and timeline complexity.
