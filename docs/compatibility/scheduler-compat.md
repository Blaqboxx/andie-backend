# Temporary Compatibility Layer: Scheduler Compat

Status: Temporary migration glue
Owner: Runtime/Scheduler
Last updated: 2026-05-29

## Purpose
Bridge legacy function-based scheduler API to the newer class-based queue backend.

## Current compatibility surface
- `scheduler/queue.py`
  - `queue_metrics()`
  - `recent_tasks(limit)`
  - `cancel_task(task_id)`
  - `request_manual_retry(task_id, preferred_node=None)`
  - `reroute_tasks(source, target, limit=100)`
- Delegates to `orchestration.queue` backend (`create_task_queue()`).

## Why it exists
Legacy modules import helpers from `scheduler.queue` while queue internals evolved.

## Removal criteria
- All call sites switched to canonical queue service interfaces.
- No imports remain from `scheduler.queue` compatibility module.

## Risks
- Compatibility mutations may bypass richer queue governance semantics.

## Migration note
Keep shim logic minimal and additive; avoid embedding core scheduling policy here.
