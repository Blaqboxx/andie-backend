# ANDIE Phase 2 Plan: Real-Time Operational Cognition

## Core Architectural Rule
- Snapshot is authoritative state.
- Stream is temporal evolution (delta events).

Never allow event frames to become the system of record.

## Achieved Foundation
- WebSocket bootstrap contract (`connection.ready` -> `workspace.snapshot`)
- Alias route normalization (`/ws/events`, `/ws/backlog` -> canonical stream behavior)
- Initial sequencing discipline via event sequence IDs
- Replay drilldown API (`GET /api/replay/{execution_id}`)
- Canonical event envelope across snapshot, stream, and replay (Phase 2A)

## Current Runtime Additions (Phase 2B/2C Bootstrap)
- Event taxonomy families are now represented in runtime validation:
	- objectives
	- governance
	- execution
	- trust
	- recovery
- Objective graph influence is active through derived runtime signals:
	- `objective.pressure`
	- `objective.critical_path`
	- `objective.blocked`
	- `objective.unblocked`
- Objective graph API surface is available:
	- `POST /api/objectives`
	- `POST /api/objectives/{objective_id}/status`
	- `GET /api/objectives/graph`

## Maturity Map (Updated)
- Phase 1: complete
	- governance stabilization
	- reliability qualification
	- promotion gates
- Phase 2A: complete
	- event envelope standardization
- Phase 2B: complete
	- event taxonomy scaffold
- Phase 2C: bootstrap complete
	- initial objective graph influence
- Phase 2D: in progress
	- memory/trust/objective-pressure coupling into governance posture

## Priority Workstreams

## 1) Live Timeline Streaming (Highest)
Goal: turn timeline from static history into live orchestration cognition.

Add event classes:
- `lifecycle.transition`
- `rollback.marker`
- `governance.escalation`
- `telemetry.stabilization`
- `confidence.update`

Acceptance:
- UI receives all timeline class events in sequence order.
- Reconnect does not produce duplicated transitions.
- Bootstrap + stream produce a coherent timeline in < 1 second after connect.

## 2) Live Confidence Curve Streaming
Goal: render trust/confidence as temporal motion, not snapshots.

Data additions:
- `confidence.value`
- `confidence.volatility`
- `confidence.decay_rate`
- `confidence.stabilization_score`

Acceptance:
- Confidence stream updates at bounded cadence.
- Volatility overlays track event pressure changes.
- No confidence divergence between snapshot and live stream after reconnect.

## 3) Replay Drilldown Expansion
Goal: operational forensics for any execution path.

API target:
- `GET /api/replay/{execution_id}`
- support optional query filters: `event_type`, `from_ts`, `to_ts`

Acceptance:
- Deterministic replay ordering by sequence.
- Replay can reconstruct escalation and recovery phases.
- Replay output suitable for timeline and governance audit views.

## 4) Event Persistence Layer
Goal: append-only event sourcing for deterministic reconstruction.

Requirements:
- append-only log writes
- monotonic sequencing
- idempotent replay reads
- snapshot checkpoint references

Acceptance:
- Crash/restart retains event log continuity.
- Replay rebuild is deterministic across runs.
- Sequence integrity checks available in diagnostics.

## 5) Controlled Autonomous Remediation (After 1-4 Stabilize)
Goal: supervised adaptive remediation with explicit governance boundaries.

Guardrails:
- remediation policy whitelist
- confidence threshold gates
- escalation guard checks
- mandatory audit trail per autonomous action

Acceptance:
- no unsupervised destructive action paths
- each remediation action is replayable and attributable

## Integration Risks to Monitor
- memory <-> posture coupling causing hidden escalation bias
- objective graph arbitration amplifying instability under load
- multi-agent contention affecting convergence timing
- reconnect storms introducing timeline skew

## Promotion Criteria to Advance Beyond Phase 2
1. Phase 1 strict gate PASS for 3 consecutive runs.
2. Timeline streaming tests PASS for 3 consecutive runs.
3. Replay determinism checks PASS for 3 consecutive runs.
4. Confidence stream integrity checks PASS for 3 consecutive runs.

## Implementation Sequence
1. Implement timeline event taxonomy + tests.
2. Add confidence stream payloads + UI consumption tests.
3. Expand replay API filters and persistence semantics.
4. Add event-sequence integrity diagnostics.
5. Introduce supervised remediation in controlled mode.
