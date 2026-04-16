from __future__ import annotations

import threading
import time
from typing import Any, Dict, List
from uuid import uuid4


_APPROVALS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def _now_ts() -> int:
    return int(time.time())


def clear_trade_approvals() -> None:
    with _LOCK:
        _APPROVALS.clear()


def _base_record(approval_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    trade = metadata.get("trade") if isinstance(metadata.get("trade"), dict) else {}
    return {
        "approvalId": approval_id,
        "status": "pending",
        "createdAt": _now_ts(),
        "resolvedAt": None,
        "trade": trade,
        "metadata": metadata,
        "lastEventType": event.get("type"),
        "events": [event.get("type")],
    }


def process_trading_approval_event(event: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(event.get("type") or "")
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}

    with _LOCK:
        if event_type == "APPROVAL_REQUIRED" and event.get("target") == "trading":
            approval_id = str(metadata.get("approvalId") or uuid4())
            event_metadata = dict(metadata)
            event_metadata["approvalId"] = approval_id
            event["metadata"] = event_metadata

            existing = _APPROVALS.get(approval_id)
            if existing is None:
                _APPROVALS[approval_id] = _base_record(approval_id, event)
            else:
                existing["status"] = "pending"
                existing["trade"] = event_metadata.get("trade") or existing.get("trade")
                existing["metadata"] = event_metadata
                existing["lastEventType"] = event_type
                existing.setdefault("events", []).append(event_type)
            return event

        approval_id = metadata.get("approvalId")
        if not approval_id or approval_id not in _APPROVALS:
            return event

        record = _APPROVALS[approval_id]
        record["lastEventType"] = event_type
        record.setdefault("events", []).append(event_type)

        if event_type in {"TRADE_EXECUTED", "TRADE_BLOCKED", "TRADE_EXECUTION_FAILED", "APPROVAL_REJECTED"}:
            record["status"] = {
                "TRADE_EXECUTED": "approved",
                "TRADE_BLOCKED": "blocked",
                "TRADE_EXECUTION_FAILED": "failed",
                "APPROVAL_REJECTED": "rejected",
            }[event_type]
            record["resolvedAt"] = _now_ts()

    return event


def list_trade_approvals(include_resolved: bool = False) -> List[Dict[str, Any]]:
    with _LOCK:
        records = list(_APPROVALS.values())

    if not include_resolved:
        records = [item for item in records if item.get("status") == "pending"]

    return sorted(records, key=lambda item: int(item.get("createdAt") or 0), reverse=True)


def get_trade_approval(approval_id: str) -> Dict[str, Any] | None:
    with _LOCK:
        record = _APPROVALS.get(approval_id)
        return dict(record) if record else None


def resolve_trade_approval(approval_id: str, status: str, actor: str | None = None, reason: str | None = None) -> Dict[str, Any] | None:
    with _LOCK:
        record = _APPROVALS.get(approval_id)
        if record is None:
            return None

        record["status"] = status
        record["resolvedAt"] = _now_ts()
        if actor:
            record["actor"] = actor
        if reason:
            record["reason"] = reason
        record.setdefault("events", []).append(f"manual:{status}")
        return dict(record)
