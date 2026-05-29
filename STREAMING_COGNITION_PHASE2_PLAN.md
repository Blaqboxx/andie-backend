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
- Phase 4C: bootstrap complete
	- fairness and aging for anti-starvation scheduling
- Phase 4D: bootstrap started
	- runtime scheduling policy profiles
- Phase 4E: bootstrap started
	- adaptive scheduler policies
- Phase 4F: bootstrap started
	- runtime optimization telemetry and bounded decay
- Phase 5A: bootstrap started
	- runtime coordinator read-only analysis layer
- Phase 5B: bootstrap started
	- objective portfolio management
- Phase 5C: bootstrap started
	- cross-portfolio arbitration
- Phase 5D: bootstrap started
	- portfolio governance overlay
- Phase 5E: bootstrap started
	- coordinator recommendation promotion
- Phase 5F: bootstrap started
	- supervisor intent integration
- Phase 5G: bootstrap started
	- governance intent review

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

## Current Runtime Additions (Phase 4C Bootstrap)
- Scheduler fairness and aging are active in arbitration.
- Aging-aware scheduler events are replay-visible:
	- `agent.supervisor_aged`
	- `agent.supervisor_boosted`
	- `agent.supervisor_starvation_detected`
	- `agent.supervisor_fairness_applied`
- Anti-starvation behavior is validated by regression coverage that proves workflow ownership rotates over repeated arbitration cycles.

## Current Runtime Additions (Phase 4D Bootstrap)
- Scheduler policies are now first-class runtime controls.
- Scheduler policy profiles are available:
	- throughput
	- balanced
	- fair
	- mission_critical
- Scheduler policy API surface is available:
	- `GET /api/agents/scheduler/policy`
	- `POST /api/agents/scheduler/policy`
- Scheduler policy application is replay-visible via:
	- `agent.scheduler_policy_applied`
- Policy fields currently shape arbitration through:
	- fairness curve
	- starvation recovery mode
	- preemption policy
	- fairness window
	- starvation threshold

## Current Runtime Additions (Phase 4E Bootstrap)
- Scheduler policy adaptation is now supported when `adaptive_mode` is enabled.
- Adaptive policy events are replay-visible:
	- `agent.scheduler_policy_changed`
	- `agent.scheduler_policy_escalated`
	- `agent.scheduler_policy_relaxed`
	- `agent.scheduler_policy_recommended`
- Arbitration can now promote policy changes under starvation pressure and emit the resulting policy transition for replay and audit.

## Current Runtime Additions (Phase 4F Bootstrap)
- Scheduler optimization state is now policy-scoped and workspace-scoped.
- Optimization telemetry events are replay-visible:
	- `agent.scheduler_confidence`
	- `agent.scheduler_effectiveness_scored`
	- `agent.scheduler_decay_applied`
	- `agent.scheduler_contention_smoothed`
- Adaptive scheduling now records bounded optimization history and effectiveness scores.
- Bounded decay now relaxes elevated scheduler profiles after quiet arbitration cycles.

## Current Runtime Additions (Phase 5A Bootstrap)
- A read-only runtime coordinator layer is now available.
- Coordinator event family is replay-visible:
	- `coordinator.recommendation_created`
	- `coordinator.priority_ranked`
	- `coordinator.blocked_objective_detected`
	- `coordinator.merge_candidate_detected`
	- `coordinator.suspension_recommended`
	- `coordinator.escalation_recommended`
- Coordinator API surface is available:
	- `GET /api/coordinator/state`
	- `GET /api/coordinator/recommendations`
	- `POST /api/coordinator/analyze`
- Coordinator analysis consumes objective signals, workflow health, scheduler policy context, governance state, and trust state to produce recommendations without invoking execution actions.

## Current Runtime Additions (Phase 5B Bootstrap)
- Coordinator analysis now builds objective portfolios from dependency-connected objective clusters.
- Portfolio state is now available in coordinator output:
	- objective_portfolios
	- portfolio_ranking
	- portfolio_health
- Portfolio event family is replay-visible:
	- `coordinator.portfolio_created`
	- `coordinator.portfolio_ranked`
	- `coordinator.portfolio_blocked`
	- `coordinator.portfolio_risk_detected`
	- `coordinator.portfolio_health_updated`
- Portfolio recommendations remain read-only and governance-aware.

## Current Runtime Additions (Phase 5C Bootstrap)
- Coordinator now emits cross-portfolio arbitration signals without taking execution authority.
- Cross-portfolio arbitration outputs are now available in coordinator analysis:
	- cross_portfolio_dependencies
	- portfolio_resource_conflicts
- Phase 5C event family is replay-visible:
	- `coordinator.portfolio_priority_changed`
	- `coordinator.portfolio_dependency_detected`
	- `coordinator.portfolio_resource_conflict_detected`
	- `coordinator.portfolio_escalation_recommended`
	- `coordinator.portfolio_suspension_recommended`
- Portfolio arbitration recommendations remain advisory and governance-aware.

## Current Runtime Additions (Phase 5D Bootstrap)
- Portfolio governance overlay now shapes coordinator recommendations by active governance profile.
- Policy-shaped coordinator outputs are now available in coordinator analysis:
	- portfolio_policy
	- portfolio_policy_conflicts
	- portfolio_suppressed_recommendations
- Phase 5D event family is replay-visible:
	- `coordinator.portfolio_governance_review_required`
	- `coordinator.portfolio_recommendation_suppressed`
	- `coordinator.portfolio_policy_applied`
	- `coordinator.portfolio_policy_conflict_detected`
- Recommendation shaping remains advisory and read-only; execution authority is unchanged.

## Current Runtime Additions (Phase 5E Bootstrap)
- Coordinator now derives advisory supervisor intent candidates from portfolio recommendations.
- Promotion-gate outputs are now available in coordinator analysis:
	- intent_candidates
	- intent_promotions
- Phase 5E event family is replay-visible:
	- `coordinator.intent_candidate_created`
	- `coordinator.intent_promotion_requested`
	- `coordinator.intent_promotion_denied`
	- `coordinator.intent_promotion_approved`
- Intent promotion remains governance-gated and advisory; no direct supervisor command authority is introduced.

## Current Runtime Additions (Phase 5F Bootstrap)
- Supervisor intake contract now receives approved advisory intents as pending intake records.
- Supervisor intake fields include promotion linkage:
	- intent_id
	- intent_type
	- source
	- portfolio_id
	- workspace_id
	- promotion_event_id
	- created_at
	- status
- Supervisor intake API surface is available:
	- `GET /api/supervisor/intents`
	- `POST /api/supervisor/intents/{intent_id}/status`
- Phase 5F event family is replay-visible:
	- `supervisor.intent_received`
	- `supervisor.intent_acknowledged`
	- `supervisor.intent_rejected`
	- `supervisor.intent_expired`
- Rejected intent transitions require `reason_code` for auditability.

## Current Runtime Additions (Phase 5G Bootstrap)
- Governance intent review is now inserted between intent promotion approval and supervisor intake.
- Governance review output is now available in coordinator analysis:
	- governance_intent_reviews
- Phase 5G event family is replay-visible:
	- `governance.intent_review_started`
	- `governance.intent_review_approved`
	- `governance.intent_review_denied`
- Review-denied intents do not emit `supervisor.intent_received`, preserving governance-first handoff integrity.

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
