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
