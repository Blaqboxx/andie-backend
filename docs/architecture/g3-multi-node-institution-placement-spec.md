# G3.4 Multi-Node Institution Placement Specification (Frozen)

Status: Frozen
Effective Date: 2026-05-30
Milestone Tag: valhalla-g3-multi-node-institution-placement-spec

## Purpose

Define the placement contract for verified institutions across Blaqtower1, Blaqtower2, and Blaqtower3 without changing G3 workflow semantics or G3.3 transport guarantees.

## Placement Rule

G3.3 changed where institutions can communicate.
G3.4 changes where institutions are placed.

The workflow contract, transport semantics, audit rules, and governance constraints remain unchanged.

## Scope

In scope:
- Intentional assignment of institution responsibility to verified nodes.
- Node-aware placement mapping for Workshop, Academy, Sentinel, Executive, Governance, Identity, Scheduler, Mission Control, and Inference services.
- Failure-impact visibility when a node is unavailable.
- Placement-level audit and replay annotations.

Out of scope:
- New A2A message semantics.
- New transport semantics.
- Scheduler authority expansion.
- Remote world mutation authority.
- Automatic self-discovery or self-migration.
- Distributed executive control.

## Verified Node Placement Map

Canonical placement targets:
- Blaqtower1 / blaqtower: Academy, Sentinel, Workshop-support, monitoring, MCP services.
- Blaqtower2: Executive, Governance, Identity, Scheduler, Mission Control, Workshop-runtime.
- Blaqtower3: Inference, model serving, embeddings, vision workloads.

## Non-Negotiable Constraints

1. Placement must not change workflow meaning.
2. Placement must not bypass governance or identity.
3. Placement must not grant direct world mutation authority.
4. Placement must preserve `session_id` and `correlation_id` continuity.
5. Placement must remain auditable and replayable.
6. Verified infrastructure is required before placement is considered authoritative.

## Placement Semantics

1. Institutions may be assigned to a primary node and optional support nodes.
2. One institution workflow may cross nodes only through the frozen G3.3 transport contract.
3. If a node is unavailable, workflows must fail deterministically under existing G3 timeout and retry semantics.
4. Placement decisions must be represented as metadata, not as new runtime authority.

## Audit Requirements

Placement records must include:
- institution_id
- primary_node
- support_nodes (if any)
- reason
- verified_at
- verified_by
- correlation_id (when tied to a workflow)
- session_id (when tied to a workflow)

## Reliability Requirements

1. Cross-node workflow behavior must remain semantically equivalent to local workflow behavior.
2. Node outage must not cause hidden partial execution.
3. Retry and timeout handling remain governed by G3.3.
4. Placement changes must be replayable as configuration history.

## Exit Criteria For G3.4

1. Verified institutions are mapped to verified nodes.
2. Placement metadata is persisted and auditable.
3. Workflow replay can explain where each institution was located.
4. Node failure impact is visible in operator-facing summaries.
5. No new workflow semantics are introduced.

## Change Control

Any change to the placement contract requires:
1. architecture update,
2. placement-aware conformance tests,
3. a new freeze tag.
