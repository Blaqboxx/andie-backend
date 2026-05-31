# G3.5 Multi-Node Institution Deployment Release Hardening

Status: In Progress
Effective Date: 2026-05-30
Related Contracts:
- docs/architecture/g3-multi-node-institution-deployment-spec.md
- docs/architecture/g3-multi-node-institution-placement-spec.md

## Purpose

Operationally verify that live multi-node execution on Blaqtower1, Blaqtower2, and Blaqtower3 behaves like the frozen G3.5 contract.

This stage does not introduce new workflow, transport, governance, or identity features.

## Hardening Objectives

1. Prove live cross-node workflows preserve session and correlation continuity.
2. Prove outage behavior is deterministic, auditable, and replayable.
3. Measure cross-node latency from replay evidence.
4. Confirm deployment topology and route lookup match canonical placement.
5. Capture release evidence in a reproducible artifact.

## Canonical Placement Under Test

- workshop -> blaqtower2
- academy -> blaqtower1
- inference -> blaqtower3

## Evidence Harness

Use scripts/g35_release_hardening.py to produce a machine-readable hardening report.

Use scripts/g35_release_hardening_preflight.sh before any hardening run to verify environment readiness.

### Preconditions

1. API reachable on the selected base URL.
2. Inter-node mode configured on the coordinator node:
- ANDIE_A2A_TRANSPORT_MODE=inter_node
- ANDIE_A2A_LOCAL_NODE_ID=blaqtower2
- ANDIE_A2A_INSTITUTION_NODES includes workshop, academy, inference mappings.
- ANDIE_A2A_NODE_ENDPOINTS includes remote node API endpoints.
3. Academy and Inference workflows available remotely.

### Standard Run (No Outage Injection)

python3 scripts/g35_release_hardening.py \
  --base-url http://127.0.0.1:8000 \
  --require-inter-node-mode \
  --outage-mode none \
  --output artifacts/g35/release_hardening_standard.json

### Preflight Gate

Run this first on the coordinator host:

./scripts/g35_release_hardening_preflight.sh

### Simulated Outage Gate

python3 scripts/g35_release_hardening.py \
  --base-url http://127.0.0.1:8000 \
  --require-inter-node-mode \
  --outage-mode simulate \
  --output artifacts/g35/release_hardening_simulated_outage.json

### Live Outage Gate

Use this mode only during a controlled maintenance window where Academy on Blaqtower1 is intentionally unavailable.

python3 scripts/g35_release_hardening.py \
  --base-url http://127.0.0.1:8000 \
  --require-inter-node-mode \
  --outage-mode live \
  --output artifacts/g35/release_hardening_live_outage.json

## Required Hardening Evidence

The report must confirm:

1. Deployment topology endpoint availability and canonical mapping.
2. Route lookup correctness for workshop, academy, and inference.
3. Workshop -> Academy continuity:
- session_id continuity
- correlation_id continuity
- replay edge sequence
- deployment metadata continuity
4. Workshop -> Academy -> Inference continuity:
- session_id continuity
- correlation_id continuity
- replay edge sequence
- deployment metadata continuity
5. Outage behavior:
- workflow status timed_out
- replay found
- replay count >= 1

## Exit Criteria For G3.5 Release Hardening

1. A standard hardening report passes on live multi-node execution.
2. An outage-mode hardening report passes with deterministic timed_out outcomes.
3. Evidence artifacts are archived with operator, timestamp, and environment context.
4. No contract changes are introduced during hardening.

## Blocker Handling

If hardening cannot execute live, record blockers explicitly and do not advance to final release tag.

Typical blockers:
- coordinator API not reachable.
- coordinator healthz endpoint unavailable.
- academy/inference node APIs not reachable.
- missing inter-node environment variables.
- missing non-interactive SSH access to Blaqtower1/Blaqtower3.

Until these blockers are cleared, only preflight and dry-run evidence should be collected.

## Current State Snapshot (2026-05-30)

- Coordinator hardening endpoint is reachable and configured in inter-node mode.
- Topology and route lookup checks pass for workshop, academy, and inference placement.
- When academy/inference node APIs are unavailable, workflow probes return deterministic `timed_out` outcomes with replay evidence instead of transport exception 500s.
- Final hardening pass is still blocked on live Blaqtower1/Blaqtower3 API and SSH availability.

## Next Step

After hardening evidence is captured and reviewed, publish final G3.5 release tag.
