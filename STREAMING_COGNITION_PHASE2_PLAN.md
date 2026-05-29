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
- Phase 2D: bootstrap complete
	- memory/trust/objective-pressure coupling into governance posture
- Phase 2E: bootstrap complete
	- governance policy overlay layer + profile application events
- Phase 2F: bootstrap complete
	- workspace-scoped governance profile bindings + profile provenance
- Phase 3: foundation started
	- agent role contracts + lifecycle event family
- Phase 3B: bootstrap complete
	- objective-to-agent arbitration with strategy visibility
- Phase 3C: bootstrap complete
	- governance-aware agent selection + decision context events
- Phase 3D: bootstrap complete
	- multi-agent collaboration planning
- Phase 3E: bootstrap complete
	- dynamic workflow adaptation
- Phase 3F: bootstrap complete
	- delegation, review chains, and consensus scaffolding
- Phase 4: foundation started
	- workflow supervisor layer
- Phase 4B: bootstrap complete
	- runtime resource arbitration across workflows

## Current Runtime Additions (Phase 2E Bootstrap)
- Governance policy overlay layer is now active with profile-driven coefficients.
- Supported runtime profiles:
	- balanced (frozen bootstrap baseline)
	- conservative
	- aggressive
	- mission_critical
- Governance profile APIs are available:
	- `GET /api/governance/profiles`
	- `POST /api/governance/profile/apply`
- Profile changes are replay-visible via `governance.profile_applied`.

## Current Runtime Additions (Phase 2F Bootstrap)
- Governance/trust state resolution supports workspace-scoped posture behavior.
- Profile binding is workspace-scoped, allowing concurrent profile policies.
- `governance.profile_applied` payload includes provenance fields:
	- workspace_id
	- actor
	- reason
	- correlation_id

## Current Runtime Additions (Phase 3 Foundation)
- Agent role contracts are now first-class runtime roles:
	- planner
	- execution
	- memory
	- governance
- Agent lifecycle events are part of the event taxonomy:
	- `agent.assigned`
	- `agent.completed`
	- `agent.blocked`
	- `agent.escalated`
- Agent coordination API surface is available:
	- `GET /api/agents/roles`
	- `GET /api/agents/tasks`
	- `POST /api/agents/assign`
	- `POST /api/agents/{task_id}/status`

## Current Runtime Additions (Phase 3B Bootstrap)
- Objective-to-agent arbitration is now available via:
	- `POST /api/agents/arbitrate`
- Arbitration emits replay-visible strategy events:
	- `agent.assignment_strategy`
- Strategy classes currently include:
	- pressure_based
	- trust_based
	- governance_directed
	- operator_forced

## Current Runtime Additions (Phase 3C Bootstrap)
- Governance-aware assignment constraints are now applied during arbitration.
- Arbitration emits replay-visible decision inputs via:
	- `agent.decision_context`
- Decision context records:
	- pressure score
	- trust score
	- governance band
	- workspace profile
	- selected strategy
	- selected role

## Current Runtime Additions (Phase 3D Bootstrap)
- Arbitration now emits collaboration workflow plans:
	- `agent.collaboration_plan`
- Collaboration plans include:
	- objective/task context
	- ordered workflow role chain
	- collaboration reason
	- selected strategy and role context
- Collaboration planning bootstrap patterns currently include:
	- planner -> execution
	- planner -> governance -> execution
	- governance -> planner -> execution
	- memory -> planner -> execution
	- execution -> planner

## Current Runtime Additions (Phase 3E Bootstrap)
- Workflow lifecycle events are now available:
	- `agent.workflow_started`
	- `agent.workflow_updated`
	- `agent.workflow_blocked`
	- `agent.workflow_replanned`
	- `agent.workflow_completed`
- Dynamic workflow update API surface is available:
	- `GET /api/agents/workflows`
	- `POST /api/agents/workflows/{workflow_id}/update`
- Workflows now include `workflow_pressure_score` driven by:
	- objective pressure
	- blocked step count
	- governance band
	- trust context

## Current Runtime Additions (Phase 3F Bootstrap)
- Delegation events are now available:
	- `agent.delegated`
- Review chain events are now available:
	- `agent.review_requested`
	- `agent.review_completed`
- Consensus events are now available:
	- `agent.consensus_started`
	- `agent.consensus_reached`
	- `agent.consensus_failed`
- Workflow health snapshots are replay-visible via:
	- `agent.workflow_health`
- Workflow governance API surface additions:
	- `POST /api/agents/workflows/{workflow_id}/delegate`
	- `POST /api/agents/workflows/{workflow_id}/review`
	- `POST /api/agents/workflows/{workflow_id}/consensus`

## Current Runtime Additions (Phase 4 Foundation)
- Supervisor events are now available:
	- `agent.supervisor_invoked`
	- `agent.supervisor_replanned`
	- `agent.supervisor_redelegated`
	- `agent.supervisor_resumed`
- Automatic supervisor hooks run on:
	- workflow blocked transitions
	- consensus failure outcomes
- Manual supervisor control endpoint:
	- `POST /api/agents/workflows/{workflow_id}/supervise`
- Workflow health snapshots now include:
	- `supervisor_actions`

## Current Runtime Additions (Phase 4B Bootstrap)
- Cross-workflow supervisor arbitration is now available:
	- `POST /api/agents/supervisor/arbitrate`
- Supervisor resource arbitration events are now available:
	- `agent.supervisor_prioritized`
	- `agent.supervisor_preempted`
	- `agent.supervisor_reallocated`
	- `agent.supervisor_transferred`
- Supervisor arbitration scores workflow priority from:
	- workflow pressure
	- blocked step count
	- replan count
	- governance band
	- supervisor action history

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
