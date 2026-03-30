# VALHALLA — ANDIE Execution Sandbox

VALHALLA is the secure execution layer for ANDIE.

## Components

- Controller: Orchestrates execution
- Policy Engine: Validates code (AST)
- Sentinel: Monitors execution
- Environments: Docker-based sandbox

## Flow

ANDIE → Validator → Sentinel → Docker → Output

## Status

v1 Complete:
- Safe execution
- Code validation
- Runtime monitoring
