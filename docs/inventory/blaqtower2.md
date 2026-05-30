# Blaqtower2 Node Inventory

## Verification Status

Verified from the local machine.

## Verification

- verified_at: 2026-05-30
- verified_by: operator
- confidence: verified

## Host Identity

- Hostname: Blaqtower2
- OS: Ubuntu 26.04 LTS
- Kernel: 7.0.0-15-generic

## Storage

- Active SSD: approximately 100GB class runtime drive
- HDD shared volume: approximately 1.9TB
- HDD archive volume: approximately 3.6TB

## Intended Role

- Valhalla core runtime
- Executive controller host
- Scheduler host
- Identity host
- Governance host
- Mission Control API host
- A2A router host

## Service Ownership

- executive_controller
- scheduler
- identity
- governance
- mission_control
- a2a_router

## Verification Notes

- Hardware and OS facts were confirmed locally.
- Service placement remains best-known until the deployment services on the node are enumerated directly.
- Qdrant, Redis, and any other auxiliary service placement still require node-level confirmation.

## Verification Date

- May 30, 2026
