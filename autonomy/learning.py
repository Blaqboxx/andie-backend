from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from knowledge.config import BASE_PATH


LEARNING_LOG = os.environ.get(
    "ANDIE_LEARNING_LOG",
    os.path.join(BASE_PATH, "knowledge", "learning_log.json"),
)


def _load_entries(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, list):
            return data
    except Exception:
        return []
    return []


def record_outcome(event: Dict[str, Any], action: str, result: str) -> Dict[str, Any]:
    os.makedirs(os.path.dirname(LEARNING_LOG), exist_ok=True)
    entries = _load_entries(LEARNING_LOG)
    entry = {
        "timestamp": time.time(),
        "event": event,
        "action": action,
        "result": result,
    }
    entries.append(entry)
    with open(LEARNING_LOG, "w", encoding="utf-8") as fp:
        json.dump(entries, fp, indent=2, ensure_ascii=False)
    return entry


def recent_success_rate(action: str, limit: int = 50) -> float:
    entries = _load_entries(LEARNING_LOG)
    filtered = [item for item in entries if str(item.get("action") or "") == action]
    if not filtered:
        return 0.5
    recent = filtered[-max(limit, 1) :]
    successes = sum(1 for item in recent if str(item.get("result") or "") in {"success", "ok", "executed"})
    return round(successes / len(recent), 3)


def recent_events(limit: int = 50) -> List[Dict[str, Any]]:
    entries = _load_entries(LEARNING_LOG)
    return entries[-max(limit, 1) :]


def detect_pattern(events: List[Dict[str, Any]]) -> str | None:
    failures = [event for event in events if str(event.get("result") or "") in {"failed", "blocked"}]
    reviews = [event for event in events if str(event.get("result") or "") == "review"]

    if len(failures) > 3:
        return "HIGH_FAILURE_RATE"
    if len(reviews) > 5:
        return "REVIEW_HEAVY"
    return None
