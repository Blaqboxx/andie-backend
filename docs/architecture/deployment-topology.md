# Valhalla Deployment Topology

This document separates verified physical facts from the current operational model and the deployment inventory that still needs host-level confirmation.

## Verified Physical Topology

Confirmed from the current machine and local disk layout:

- Hostname: `Blaqtower2`
- OS: Ubuntu 26.04 LTS
- Kernel: `7.0.0-15-generic`
- Active SSD: approximately `100GB` class runtime drive
- HDD storage: approximately `1.9TB` shared volume and `3.6TB` archive volume

## Operational Topology

This is the logical shape the software stack currently wants to follow:

- GPU PC: inference and model-serving compute
- NUC 2: Valhalla core runtime
- NUC 1: support and institutional services
- Storage layer: persistence, archives, and shared data

## Deployment Registry

The registry below records the current best-known placement model.

| Node | Role | Service Ownership | Confidence | Verification Status |
| --- | --- | --- | --- | --- |
| `blaqtower2` | `valhalla_core` | `executive_controller`, `scheduler`, `identity`, `governance`, `mission_control`, `a2a_router` | Medium | Needs node-level confirmation |
| `nuc1` / `Blaqtower1` (`blaqtower`) | `institutions` | `sentinel`, `academy`, `workshop`, `monitoring`, `mcp_services` | High | Verified |
| `gpu_pc` / `Blaqtower3` | `inference` | `llm_server`, `embedding_server`, `vision_models` | High | Verified |
| `active_ssd` | `runtime_state` | `executive`, `identity`, `agenda`, `sessions`, `a2a` | High | Verified as local runtime storage class |
| `shared_hdd` | `operational_data` | `shared_data`, `working_sets`, `cross_service_artifacts` | High | Verified as mounted storage class |
| `archive_hdd` | `long_term_storage` | `archives`, `backups`, `history` | High | Verified as mounted storage class |

## Unknowns Requiring Node Verification

These items should be confirmed from the nodes themselves before being treated as authoritative:

- Whether Qdrant runs on `blaqtower2`, `nuc1`, or a separate node
- Whether Redis runs locally on `blaqtower2` or on a support node
- Whether Mission Control is an API surface inside `blaqtower2` or a separate service on `nuc1`
- Whether Sentinel is deployed on `nuc1` or embedded in the core node
- Which machine hosts the LLM runtime and any embedding/vision services

## Verification Attempt Snapshot (2026-05-30)

- `Blaqtower3` was verified directly via `jamai-jamison@blaqtower3`.
- The institutions node was verified via `jamai-jamison@blaqtower` and reports hostname `Blaqtower`.
- Both nodes produced host, CPU, memory, storage, container, and running-service evidence.

## Ownership Model

- `blaqtower2` should remain the authoritative Valhalla core unless deployment evidence says otherwise.
- `nuc1` should remain the service and institution host.
- `gpu_pc` should remain the inference host.
- Service ownership should be listed explicitly so failure impact is visible without inferring it from role names.
- Storage should be treated as a persistence tier, not a compute tier.

## Verification Rules

A placement should not be promoted from "best-known" to "verified" unless it is confirmed from the node itself or from a deployment record on that node.

## Next Step

See [docs/inventory/README.md](../inventory/README.md) for node-level verification records.

The inventory records are the promotion path from logical topology to verified deployment topology.
