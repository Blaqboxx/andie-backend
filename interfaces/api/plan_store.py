from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PLANS_DIR = BACKEND_ROOT / "storage" / "plans"


def _parse_saved_at(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_filename(name: str) -> str:
    """Return a filesystem-safe base-name component (no path traversal)."""
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(name or "").strip())
    return safe[:80] or "snapshot"


def save_plan_snapshot(
    *,
    name: str,
    task: str,
    editable_plan: List[Dict[str, Any]],
    edit_trail: List[Dict[str, Any]] | None = None,
    actor: str = "operator",
    request_id: str | None = None,
) -> Dict[str, Any]:
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(name)
    ts = datetime.now(timezone.utc)
    filename = f"{ts.strftime('%Y%m%dT%H%M%S')}_{safe}.json"
    snapshot: Dict[str, Any] = {
        "name": name,
        "savedAt": ts.isoformat(),
        "savedBy": actor,
        "task": task,
        "editablePlan": editable_plan,
        "editTrail": edit_trail or [],
        "requestId": request_id,
    }
    (PLANS_DIR / filename).write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return {"filename": filename, **snapshot}


def list_plan_snapshots() -> List[Dict[str, Any]]:
    if not PLANS_DIR.exists():
        return []
    snapshots = []
    for path in sorted(PLANS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            snapshots.append(
                {
                    "filename": path.name,
                    "name": data.get("name", path.stem),
                    "savedAt": data.get("savedAt"),
                    "savedBy": data.get("savedBy"),
                    "task": data.get("task"),
                    "stepCount": len(data.get("editablePlan") or []),
                }
            )
        except Exception:
            continue
    return snapshots


def load_plan_snapshot(filename: str) -> Dict[str, Any] | None:
    # Reject any path-traversal attempts — only allow safe characters
    safe = re.sub(r"[^a-zA-Z0-9_\-.]", "", str(filename))
    if not safe.endswith(".json"):
        return None
    path = PLANS_DIR / safe
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_latest_plan_snapshot() -> Dict[str, Any] | None:
    if not PLANS_DIR.exists():
        return None

    latest_payload: Dict[str, Any] | None = None
    latest_key: tuple[float, float] | None = None

    for path in PLANS_DIR.glob("*.json"):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        saved_at = _parse_saved_at(snapshot.get("savedAt"))
        # Primary ordering key: explicit snapshot savedAt; fallback: filesystem mtime.
        order_key = (
            saved_at.timestamp() if saved_at else float("-inf"),
            float(path.stat().st_mtime),
        )

        if latest_key is None or order_key > latest_key:
            latest_key = order_key
            latest_payload = {"filename": path.name, **snapshot}

    return latest_payload
