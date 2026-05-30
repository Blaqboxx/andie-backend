# Temporary Compatibility Layer: Legacy Workflow API

Status: Temporary migration glue
Owner: API/Runtime
Last updated: 2026-05-29

## Purpose
Maintain backward compatibility for workflow routes and response contracts expected by existing tests/clients.

## Current compatibility surface
- `interfaces/api/main.py`
  - Exports `app` object for legacy imports: `from interfaces.api.main import app`
  - Adds `POST /workflow/run` for direct workflow execution.
  - Adds workflow short-circuit branch in `POST /orchestrator/run` when task text indicates workflow intent.
  - Returns legacy fields including top-level `workflowId` and nested `result.workflowId`.

## Why it exists
New orchestration and inference wiring changed route assumptions used by older consumers.

## Removal criteria
- Clients/tests migrated to canonical workflow runtime interfaces.
- Legacy response keys and alias routes no longer referenced.

## Risks
- Two API shapes can drift if not tested together.

## Migration note
No new product behavior should be introduced in compatibility branches.
