# Executive Architecture

The ExecutiveController is the single authority for mission, goal, task, and governed world transitions.

## Responsibilities
- Coordinate mission and goal lifecycle.
- Generate and dispatch plans through the planner and dispatcher.
- Enforce proposal review and execution gates for world mutation.
- Record cycle audits for budget and governance observability.

## Non-goals
- No direct institution world mutation.
- No bypass of identity checks.

## Phase Progression

Current executive maturity is tracked as:

- F3 Institutions: complete.
- G1 Alpha Agenda Stewardship: complete.
- G1 Beta Observability: complete.
- G1 Release Multi-cycle Management: complete.
- G1.1 Policy and Explainability: complete.
- G1.2 Simulation and Prediction: complete.
- G1.3 Intent Lifecycle: complete.
- G1.4 Operational Readiness: complete (initial SLO instrumentation).
- G2 Alpha Bounded Scheduler: complete.
- G2.1 Scheduler Observability: complete.
- G2.2 Intent Outcome Feedback: complete.
- G2.3 Controlled Multi-cycle Execution: complete.
- G2.4 Autonomy Session Tracking and Replay: complete.
- G3.0 Local A2A Protocol: complete.
- G3.1 Local A2A Router Conformance: complete.
- G3.2 Institution Workflow Exchange: complete.
- G3.3 Inter-Node Transport Contract: frozen.

## G1.4 Operational Readiness

Operational SLOs are now first-class executive capabilities exposed through `GET /executive/slo`.

- Executive SLOs
	- decision latency p95 target.
	- agenda rebuild time p95 target.
	- simulation latency p95 target.
- Intent SLOs
	- intent creation success target.
	- intent completion time target.
	- stale intent threshold by cycle age.
- Governance SLOs
	- policy violation rate target.
	- simulation state mutation target.
	- identity bypass attempt target.

This keeps G2 ordering disciplined: autonomy must be gated by measurable operational quality and governance integrity.

## G2 Alpha Constraints (Pre-Implementation Freeze)

Before any continuous autonomy code is introduced, G2 Alpha is constrained to a bounded scheduler only.

- Constraint 1: Scheduler cannot bypass ExecutiveAgenda.
- Constraint 2: Scheduler cannot bypass governance or identity checks.
- Constraint 3: Scheduler cannot directly mutate world state.

Permitted G2 Alpha control flow:

`Scheduler -> ExecutiveAgenda loop -> Intent -> Institution -> Proposal -> Governance gate -> World mutation`

Mandatory halt conditions for bounded scheduling:

- policy violation rate > 0.
- budget breach.
- stale intent threshold exceeded.

## G3 Entry Contract (Pre-Implementation Freeze)

Before any distributed A2A implementation begins, the following rules are frozen:

- Rule 1: Institutions may communicate, but must not bypass executive governance.
- Rule 2: Institutions may exchange requests, but must not directly mutate world state.
- Rule 3: Every inter-institution exchange must be auditable.

Mandatory audit fields for A2A exchanges:

- sender
- receiver
- timestamp
- request
- response
- session_id

G3.0 scope is a local A2A protocol only (no multi-node networking).

## G3.0 Local A2A Protocol (Initial)

Implemented as a local process protocol with no networking layer.

Frozen local protocol contract is documented in `docs/architecture/g3-local-a2a-spec.md`.

- Message model includes `message_id`, `session_id`, `sender`, `receiver`, `timestamp`, `message_type`, `request`, `response`, and `status`.
- Message ledger supports append, get-by-id, and session-scoped listing.
- Router supports send, respond, inbox, and session replay-style retrieval.
- Every send path enforces identity checks, governance restrictions, and audit write.
- Mutation-oriented message types are blocked by governance policy in local protocol mode.

## G3.1 Coordinated Local Workflows (Initial)

Built on top of G3.0 protocol primitives to prove local collaboration before any networking.

- Local workflow pattern now supports Academy -> Workshop research request and Workshop -> Academy prototype response.
- Workflow execution remains session-linked and fully auditable through the A2A ledger.
- Collaboration uses existing send/respond protocol paths and does not add direct world mutation authority.

## G3.1 Local A2A Router Conformance

The local router now enforces conformance against the frozen G3.0 contract.

- Required envelope fields enforced, including `correlation_id` and `session_id`.
- Message lifecycle aligned to contract states: `pending`, `responded`, `rejected`, `timed_out`.
- Governance and identity failures are persisted as auditable rejected messages.
- Timeout transitions are deterministic and persisted with machine-readable error codes.
- Replay and query surfaces preserve correlation chains and status transitions.

## G3.2 Institution Workflow Exchange

The local router now proves governed institution collaboration, not just message delivery.

- Workshop can delegate research work to Academy and receive a governed result back.
- Workflow exchange preserves `session_id` and `correlation_id` across the full request/response chain.
- Timeout workflows remain deterministic and replayable.
- Governance-denied workflow attempts are written to the audit ledger.
- Replay surfaces can return the complete workflow exchange for a session and correlation chain.

## G3.3 Inter-Node Transport Contract (Frozen)

Inter-node transport contract is frozen in docs/architecture/g3-inter-node-transport-spec.md.

- G3.3 preserves G3.2 workflow semantics while moving delivery across verified nodes.
- session_id and correlation_id continuity remain mandatory across hosts.
- Transport retries, failures, and acknowledgements are auditable.
- Governance and identity checks remain mandatory and unchanged.
- Transport must not introduce new institution authority or direct mutation paths.

## G3.3 Inter-Node Transport (Alpha)

First implementation keeps the adapter intentionally narrow:

- `LocalA2ARouter` remains the local semantic authority.
- `InterNodeA2ARouter` preserves the same workflow interface and delegates cross-node delivery via HTTP transport.
- Workflow semantics (`session_id`, `correlation_id`, status transitions, replay shape) remain unchanged.
- Replay includes node transport metadata while preserving workflow event order and meaning.

Alpha implementation is controlled by runtime config:

- `ANDIE_A2A_TRANSPORT_MODE=local|inter_node`
- `ANDIE_A2A_LOCAL_NODE_ID`
- `ANDIE_A2A_INSTITUTION_NODES` (JSON map)
- `ANDIE_A2A_NODE_ENDPOINTS` (JSON map)

## A2A Placement

Agent-to-Agent (A2A) collaboration is scheduled after G1 hardening and before broad distributed autonomy.

- Near-term (G2 baseline): keep bounded continuous autonomy inside the current governed runtime.
- Next (G2.1/G3 foundation): introduce A2A contracts for discovery, delegation, status, evidence return, and escalation.
- Later (distributed cognition): deploy specialized agents across nodes only after A2A protocol and governance controls are stable.

This sequencing preserves the principle: build judgment before autonomy, and collaboration before uncontrolled distribution.

## Freeze and Protection

The executive subsystem is now feature-complete for G1 intent and should be treated as a protected baseline.

- Keep identity and governance checks mandatory on decision and execution paths.
- Keep simulation strictly non-mutating (no agenda writes, no decision/intent append side effects).
- Keep intent lifecycle as the required bridge between prioritized agenda items and institution execution.
- Route escalation tuning through agenda policy, not hardcoded logic changes.
- Preserve explain and replay compatibility for all decision and intent lifecycle transitions.

## Change Control

Allowed without architecture RFC:

- Add institutions and intent consumers.
- Add agenda visualizations and operator-facing summaries.
- Extend simulation capabilities while keeping simulation non-mutating.
- Add governance policies and policy observability.
- Add autonomy scheduling and cadence controls above existing executive contracts.

Not allowed without architecture RFC:

- Replace `ExecutiveAgenda` as the canonical agenda state object.
- Replace intent lifecycle as the execution bridge.
- Remove or bypass decision ledger recording.
- Bypass identity checks on decision or execution paths.
- Bypass governance checks on decision or execution paths.
- Mutate agenda or append decision/intent history from simulation surfaces.
- Introduce scheduler pathways that bypass agenda, intent, proposal, or governance gates.
