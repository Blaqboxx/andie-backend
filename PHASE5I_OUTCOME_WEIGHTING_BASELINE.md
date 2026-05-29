# Phase 5I Outcome Weighting Baseline

Date: 2026-05-29
Status: COMPLETE
Freeze Eligible: YES

## Scope Locked

Phase 5I delivers outcome-memory-backed recommendation weighting with governance-scoped isolation and replay-visible explainability.

Included:
- Outcome registry and modifier computation.
- Governance and intent isolation in weighting lookup.
- Optimizer consumption of modifier for final recommendation score.
- Runtime persistence across backend restart.
- Replay-visible outcome-weight event and score metadata.

Excluded:
- Policy mutation.
- Automatic execution.
- Platform-wide propagation beyond current integrated optimizer path.

## Acceptance Evidence

### 1) Live Optimize Execution

Endpoint: POST /autonomy/optimize

Observed result:
- status: ok
- execution_id: phase5i-replay-gate-proof-1780074077

### 2) Live Replay Reconstruction

Endpoint: GET /api/replay/{execution_id}

Observed result:
- found: true
- event_count: 1
- event type: coordinator.outcome_weight_applied
- base_score: 0.6833
- outcome_weight_modifier: 0.0441
- final_score: 0.7274

### 3) Restart Persistence

Before restart and after restart checks returned identical scoring inputs/outputs for the probe context:
- modifier_before == modifier_after
- final_score_before == final_score_after

This confirms outcome weighting is persisted, not memory-only.

## Deployment Namespace Fix Record

Runtime import audit in container showed:
- autonomy.*: not importable
- interfaces.*: not importable
- andie_backend.autonomy.*: importable
- andie_backend.interfaces.*: importable

Route-level import resolution was updated in optimize path to prefer deployment namespace and retain fallback compatibility for source-layout execution.

## Replay Schema Baseline

Replay event for Phase 5I weighting is expected to include:
- type: coordinator.outcome_weight_applied or coordinator.outcome_weight_unavailable
- execution_id
- base_score
- outcome_weight_modifier
- final_score
- candidate_skill
- intent_type
- governance_profile
- portfolio_group

## Scoring Guardrails

Modifier bounds are preserved at:
- minimum: -0.15
- maximum: +0.15

## Governance Isolation Guarantee

Weighting lookup and registry partitioning are scoped by:
- intent_type
- governance_profile
- optional portfolio_group

No cross-scope bleed is expected between distinct governance or intent domains.

## Rollback Anchor

Baseline label:
- PHASE5I_OUTCOME_WEIGHTING_BASELINE

This document is the freeze reference point before Phase 5J expansion.
