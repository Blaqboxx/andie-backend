"""Backend event schema validation.

Validates outbound event payloads before they are enriched and broadcast to
SSE/WebSocket subscribers.  A payload that fails required-field checks is
rejected (ValueError raised by the caller); optional-field type mismatches
are logged as warnings but do not block publication.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

_log = logging.getLogger(__name__)

# Fields that must be present and must be non-empty strings.
_REQUIRED_STRING_FIELDS: tuple[str, ...] = ("type",)

# Optional scalar fields — reject lists/dicts masquerading as scalar values.
_OPTIONAL_SCALAR_FIELDS: tuple[str, ...] = (
    "agent",
    "actor",
    "caller",
    "target",
    "message",
    "reason",
    "workflowId",
    "workflow_id",
    "taskId",
    "task_id",
    "level",
    "status",
)

# Recognised severity levels (informational — not enforced as errors).
_KNOWN_LEVELS: frozenset[str] = frozenset(
    {"info", "warn", "warning", "error", "critical", "ok", "debug", "neutral"}
)


def validate_event_payload(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate *payload* before enrichment and broadcast.

    Returns ``(is_valid, issues)`` where *is_valid* is ``False`` when a
    required field is absent or has the wrong type, and *issues* is a list of
    human-readable problem descriptions (both blocking errors and warnings).
    """
    if not isinstance(payload, dict):
        return False, [f"payload must be a dict, got {type(payload).__name__}"]

    blocking: List[str] = []
    warnings: List[str] = []

    # ── required fields ──────────────────────────────────────────────────────
    for field in _REQUIRED_STRING_FIELDS:
        value = payload.get(field)
        if value is None:
            blocking.append(f"required field '{field}' is missing")
        elif not isinstance(value, str) or not value.strip():
            blocking.append(
                f"required field '{field}' must be a non-empty string, got {value!r}"
            )

    # ── optional scalar fields ────────────────────────────────────────────────
    for field in _OPTIONAL_SCALAR_FIELDS:
        value = payload.get(field)
        if value is not None and isinstance(value, (dict, list)):
            warnings.append(
                f"field '{field}' should be a scalar value, got {type(value).__name__}"
            )

    # ── informational: unknown level ──────────────────────────────────────────
    level = payload.get("level")
    if level is not None and str(level).lower() not in _KNOWN_LEVELS:
        _log.debug(
            "event type=%r uses non-standard level=%r", payload.get("type"), level
        )

    issues = blocking + warnings
    return len(blocking) == 0, issues
