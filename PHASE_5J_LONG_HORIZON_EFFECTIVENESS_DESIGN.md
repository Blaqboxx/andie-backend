# Phase 5J Long-Horizon Effectiveness Aggregation

Date: 2026-05-29
Status: Design Start
Precondition: Phase 5I frozen

## Objective

Add long-horizon effectiveness measurement so recommendation weighting can account for trend and baseline quality over time windows, not only immediate aggregate recency.

## Non-Goals

- No increase to modifier cap beyond current safety bounds without explicit governance approval.
- No automatic action execution expansion.
- No policy mutation behavior in this phase.

## Proposed Aggregation Dimensions

- 30-day effectiveness by intent_type and governance_profile.
- 90-day effectiveness by intent_type and governance_profile.
- portfolio_group effectiveness slices.
- intent family rollups.
- governance profile rollups.

## Proposed Telemetry Events

Replay-visible coordinator events:
- coordinator.effectiveness_trend_updated
- coordinator.effectiveness_baseline_updated

Suggested event payload fields:
- execution_id
- intent_type
- governance_profile
- portfolio_group
- window_days
- baseline_effectiveness
- trend_effectiveness
- delta
- sample_count
- timestamp

## Architecture Outline

1. Aggregation Store
- Add time-windowed counters and averages keyed by:
  - intent_type
  - governance_profile
  - optional portfolio_group
  - window bucket

2. Update Path
- Extend outcome ingestion path to append/update rolling-window aggregates.

3. Optimizer Consumption
- Keep current Phase 5I modifier pipeline.
- Add optional trend factor as informational output first.
- Promote to bounded scoring contribution only after validation.

4. Replay Integration
- Emit trend/baseline update events whenever aggregate windows are updated.
- Ensure replay serializer preserves all trend metadata fields.

## Guardrails

- Preserve existing Phase 5I modifier hard bounds: [-0.15, +0.15].
- If Phase 5J introduces additive influence, apply a separate bounded cap.
- Require minimum sample_count threshold before trend influence can be active.
- Default to neutral influence when data is sparse.

## Validation Plan

1. Unit validation
- Window aggregation math for 30-day and 90-day ranges.
- Isolation tests for intent/governance/portfolio partitions.

2. Runtime validation
- Live optimize call emits unchanged behavior when trend factor disabled.
- Replay shows trend/baseline events with complete payload.

3. Persistence validation
- Aggregate windows survive backend restart.
- Baseline and trend values remain stable across restart.

## Exit Criteria

- Replay includes coordinator.effectiveness_trend_updated and coordinator.effectiveness_baseline_updated with required fields.
- Aggregation windows and baselines persist across restart.
- No regression to Phase 5I optimize status or replay metadata.
