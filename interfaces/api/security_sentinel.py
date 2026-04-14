from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Dict


AUTH_HEADER = "Authorization"
CALLER_HEADER = "X-ANDIE-Caller"
TIMESTAMP_HEADER = "X-ANDIE-Timestamp"
SIGNATURE_HEADER = "X-ANDIE-Signature"
DEFAULT_SHARED_SECRET = "andie-dev-shared-secret"
TOKEN_TTL_SECONDS = int(os.environ.get("ANDIE_CLUSTER_TOKEN_TTL_SECONDS", "60"))


def shared_secret() -> str:
    return os.environ.get("ANDIE_CLUSTER_SHARED_SECRET", DEFAULT_SHARED_SECRET)


def local_node_id() -> str:
    return os.environ.get("ANDIE_LOCAL_NODE_ID", "thinkpad")


def allowed_worker_callers() -> set[str]:
    raw = os.environ.get("ANDIE_WORKER_ALLOWED_CALLERS", "thinkpad")
    return {item.strip() for item in raw.split(",") if item.strip()}


def security_audit_log_path() -> Path:
    return Path(
        os.environ.get(
            "ANDIE_SECURITY_AUDIT_LOG",
            Path(__file__).resolve().parent.parent.parent / "logs" / "security-audit.log",
        )
    )


def canonical_payload(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def build_signature(caller: str, timestamp: str, payload: Any, secret: str | None = None) -> str:
    material = f"{caller}:{timestamp}:{canonical_payload(payload)}".encode("utf-8")
    return hmac.new((secret or shared_secret()).encode("utf-8"), material, hashlib.sha256).hexdigest()


def bearer_token(caller: str, timestamp: str, signature: str) -> str:
    return f"Bearer {caller}:{timestamp}:{signature}"


def signed_headers(payload: Any, caller: str | None = None, timestamp: int | None = None) -> Dict[str, str]:
    caller_id = caller or local_node_id()
    ts = str(timestamp if timestamp is not None else int(time.time()))
    signature = build_signature(caller_id, ts, payload)
    token = bearer_token(caller_id, ts, signature)
    return {
        AUTH_HEADER: token,
        CALLER_HEADER: caller_id,
        TIMESTAMP_HEADER: ts,
        SIGNATURE_HEADER: signature,
    }


def parse_bearer_token(auth_header: str | None) -> tuple[str | None, str | None, str | None]:
    if not auth_header or not auth_header.startswith("Bearer "):
        return None, None, None
    token_value = auth_header[len("Bearer ") :]
    parts = token_value.split(":", 2)
    if len(parts) != 3:
        return None, None, None
    return parts[0], parts[1], parts[2]


def verify_signed_headers(headers: Dict[str, str] | Any, payload: Any) -> tuple[bool, str, str | None]:
    auth_header = headers.get(AUTH_HEADER)
    header_caller = headers.get(CALLER_HEADER)
    header_timestamp = headers.get(TIMESTAMP_HEADER)
    header_signature = headers.get(SIGNATURE_HEADER)

    token_caller, token_timestamp, token_signature = parse_bearer_token(auth_header)
    if not token_caller or not token_timestamp or not token_signature:
        return False, "missing_or_invalid_bearer_token", None

    if header_caller != token_caller or header_timestamp != token_timestamp or header_signature != token_signature:
        return False, "header_token_mismatch", token_caller

    try:
        ts_value = int(token_timestamp)
    except ValueError:
        return False, "invalid_timestamp", token_caller

    if abs(int(time.time()) - ts_value) > TOKEN_TTL_SECONDS:
        return False, "expired_token", token_caller

    expected = build_signature(token_caller, token_timestamp, payload)
    if not hmac.compare_digest(expected, token_signature):
        return False, "invalid_signature", token_caller

    return True, "ok", token_caller


def audit_security_event(event_type: str, details: Dict[str, Any]) -> None:
    path = security_audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": int(time.time()),
        "event": event_type,
        **details,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def authorize_worker_request(headers: Dict[str, str] | Any, payload: Any) -> tuple[bool, str, str | None]:
    valid, reason, caller = verify_signed_headers(headers, payload)
    if not valid:
        audit_security_event("worker_auth_rejected", {"reason": reason, "caller": caller})
        return False, reason, caller

    if caller not in allowed_worker_callers():
        audit_security_event("worker_auth_rejected", {"reason": "caller_not_allowed", "caller": caller})
        return False, "caller_not_allowed", caller

    audit_security_event("worker_auth_accepted", {"caller": caller})
    return True, "ok", caller