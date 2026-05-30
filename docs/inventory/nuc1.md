# Blaqtower1 Node Inventory

## Verification Status

Verified from remote node inspection over SSH.

## Host Identity

- Hostname: Blaqtower
- Aliases: Blaqtower1, NUC 1

## Verification

- verified_at: 2026-05-30
- verified_by: operator
- confidence: verified

## Platform Facts

- Kernel: 6.17.0-29-generic
- Architecture: x86_64
- CPU: Intel Celeron 847 (2 cores)
- RAM: 7.6Gi total

## Storage

- Root filesystem (`/dev/sda2`): 109G total, 89G used, 15G available
- EFI partition (`/dev/sda1`): 1.1G total

## Intended Role

- Institutional services host
- Sentinel host
- Workshop services host
- Academy services host
- Monitoring host
- MCP services host

## Service Ownership

- sentinel
- academy
- workshop
- monitoring
- mcp_services

## Observed Runtime Evidence

- Containers observed: `cryptonia-sentinel`, `cryptonia-dashboard`, `cryptonia-event-bus`, `cryptonia-paper-broker`, `cryptonia-market-ingestion`, `cryptonia-momentum-agent`, `cryptonia-strategy-brain-1`
- Running services observed include: `docker.service`, `redis-server.service`, `sentinel.service`, `netdata.service`, `ollama.service`, `tailscaled.service`

## Fields To Verify

- Hostname
- OS
- Kernel
- CPU
- RAM
- GPU presence, if any
- Storage
- Running services
- Open ports
- Containers
- Verification date

## Current Confidence

- High

## Notes

- Treat all service placement here as best-known, not authoritative.
- Tailnet endpoint used for verification: `blaqtower`.
