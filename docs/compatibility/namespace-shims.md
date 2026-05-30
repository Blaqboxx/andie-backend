# Temporary Compatibility Layer: Namespace Shims

Status: Temporary migration glue
Owner: Platform/API
Last updated: 2026-05-29

## Purpose
Preserve legacy import contracts while architecture transitions to new package boundaries.

## Current shims
- `andie_backend/__init__.py`
  - Extends package path to support both `andie_backend.*` and root-level module resolution in CI/local discovery contexts.

## Why it exists
Older callers and tests still import via `andie_backend.*` while some modules remain rooted at repository top-level.

## Removal criteria
- All runtime and test imports standardized to one canonical package root.
- CI passes without `andie_backend/__init__.py` namespace extension behavior.

## Risks
- Can mask import hygiene regressions if kept indefinitely.

## Migration note
Treat as compatibility surface only; do not add new architecture features here.
