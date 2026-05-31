# G3.5 Multi-Node Institution Deployment Specification (Frozen)

Status: Frozen
Effective Date: 2026-05-30
Milestone Tag: valhalla-g3-multi-node-institution-deployment-spec

## Purpose

Define deployment behavior for running institutions from their assigned nodes while preserving all previously frozen guarantees from governance, identity, workflow, transport, and placement contracts.

## Deployment Rule

G3.4 changed where institutions are assigned.
G3.5 changes where institutions execute.

Workflow semantics, transport semantics, governance behavior, identity behavior, and audit requirements remain unchanged.

## Scope

In scope:
- Runtime execution of institutions from canonical assigned nodes.
- Deployment-level proof gates for cross-node workflow continuity.
- Deployment metadata needed for audit and replay explanation.
- Deterministic failure behavior for node unavailability.

Out of scope:
- New workflow message semantics.
- New transport semantics or authority.
- Governance policy bypass behavior.
- Identity policy bypass behavior.
- Scheduler authority expansion.
- Direct institution-to-database mutation rights.

## Canonical Deployment Topology

Execution targets:
- Blaqtower1: Academy, Sentinel, Support Services.
- Blaqtower2: Executive, Governance, Identity, Scheduler, Mission Control, Workshop.
- Blaqtower3: Inference, Model Services.

## Non-Negotiable Invariants

The following must remain true under deployment:
1. `session_id` preserved.
2. `correlation_id` preserved.
3. Audit trail preserved.
4. Identity validation preserved.
5. Governance validation preserved.
6. Replay integrity preserved.

The following are not allowed:
1. Direct institution-to-database mutation.
2. Cross-node governance bypass.
3. Cross-node identity bypass.
4. Scheduler authority expansion.
5. Transport-generated business side effects.

## Deployment Proof Gates

### Gate 1: Workshop -> Academy cross-node completion

Path:
- Blaqtower2 -> Blaqtower1.

Requirement:
- Workflow completes successfully under standard governance and identity checks.

### Gate 2: Workshop -> Academy -> Workshop replay continuity

Requirement:
- Replay remains valid with the same `correlation_id`, same `session_id`, and same workflow outcome.

### Gate 3: Workshop -> Academy -> Inference distributed chain

Path:
- Blaqtower2 -> Blaqtower1 -> Blaqtower3.

Requirement:
- Audit continuity is preserved end-to-end across all hops.

### Gate 4: Node failure determinism

Condition:
- Academy unavailable.

Required outcome:
- `timed_out`, audited, replayable.

Forbidden outcome:
- hung, unknown, or partial semantic completion.

### Gate 5: Replay equivalence across deployment modes

Requirement:
- Completed workflow replay is semantically equivalent between single-node and multi-node execution, except deployment metadata.

## Deployment Audit Requirements

Deployment records must include:
- institution_id
- assigned_node
- executing_node
- deployment_mode
- session_id
- correlation_id
- outcome_status
- verified_at
- verified_by

## Success Criteria For G3.5

G3.5 is complete when:
1. Academy executes on Blaqtower1.
2. Executive executes on Blaqtower2.
3. Inference executes on Blaqtower3.
4. End-to-end workflows preserve all frozen invariants.
5. Multi-node behavior is semantically equivalent to single-node behavior except deployment metadata.

## Change Control

Any change to deployment behavior contract requires:
1. architecture update,
2. deployment-aware conformance tests,
3. a new freeze tag.
