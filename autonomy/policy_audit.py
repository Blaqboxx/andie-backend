from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class PolicyAuditLogger:
    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            root = Path(__file__).resolve().parent.parent
            path = root / "storage" / "logs" / "policy_audit.log"
        self.path = Path(path)

    def log_event(
        self,
        *,
        actor: str,
        action: str,
        previous: Any,
        new: Any,
        reason: str,
        request_id: str | None = None,
    ) -> Dict[str, Any]:
        event: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "previous_state": previous,
            "new_state": new,
            "reason": reason,
        }
        if request_id:
            event["request_id"] = request_id

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")
        return event


audit_logger = PolicyAuditLogger()