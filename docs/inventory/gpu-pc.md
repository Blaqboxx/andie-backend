# Blaqtower3 Node Inventory

## Verification Status

Verified from remote node inspection over SSH.

## Host Identity

- Hostname: Blaqtower3
- Alias: GPU PC

## Verification

- verified_at: 2026-05-30
- verified_by: operator
- confidence: verified

## Platform Facts

- Kernel: 7.0.0-15-generic
- Architecture: x86_64
- CPU: AMD A8-3800 APU (4 cores)
- RAM: 11Gi total

## Storage

- Root filesystem (`/dev/sda3`): 126G total, 28G used, 91G available
- EFI partition (`/dev/sda2`): 1.1G total

## Intended Role

- Inference and model-serving host
- LLM runtime host
- Embedding service host
- Vision model host
- Future fine-tuning host

## Service Ownership

- llm_server
- embedding_server
- vision_models

## Observed Runtime Evidence

- Docker engine active; no running containers observed at verification time.
- Running services observed include: `docker.service`, `containerd.service`, `ollama.service`, `tailscaled.service`

## Fields To Verify

- Hostname
- OS
- Kernel
- CPU
- GPU
- VRAM
- RAM
- Storage
- Running services
- Open ports
- Containers
- Verification date

## Current Confidence

- High

## Notes

- Treat all service placement here as best-known, not authoritative.
- Verified through `jamai-jamison@blaqtower3` over SSH from `Blaqtower2`.

## 2026-05-30 Runtime Probe Delta

- Host responded to interactive terminal probe from operator session.
- Open ports observed during probe: `22`, `11434`.
- No running containers observed during probe.
- Ollama runtime remains present on port `11434`.
- No ANDIE API responded on `127.0.0.1:8000` or `127.0.0.1:8010` during probe.
- Current hardening interpretation: Blaqtower3 is serving inference runtime capacity, but is not yet exposing the required ANDIE A2A node API.
