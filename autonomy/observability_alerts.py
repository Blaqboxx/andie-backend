from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict

from autonomy.control_plane_metrics import control_plane_metrics
from autonomy.runtime_config import get_runtime_config


ALERT_METRIC_KEYS = {
    "outcome_ingestion_failure": "alert_outcome_ingestion_failures",
    "score_drift_spike": "alert_score_drift_spikes",
    "memory_write_error": "alert_memory_write_errors",
}


def observability_alert_log_path() -> Path:
    return Path(
        os.environ.get(
            "ANDIE_OBSERVABILITY_ALERT_LOG",
            Path(__file__).resolve().parent.parent / "logs" / "observability-alerts.log",
        )
    )


def _alerts_enabled() -> bool:
    config = get_runtime_config()
    if "observability_alerts_enabled" in config:
        return bool(config.get("observability_alerts_enabled"))
    raw = os.environ.get("ANDIE_OBSERVABILITY_ALERTS_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def emit_observability_alert(
    alert_type: str,
    message: str,
    severity: str = "warning",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not _alerts_enabled():
        return {"emitted": False, "reason": "alerts_disabled"}

    entry = {
        "timestamp": int(time.time()),
        "type": str(alert_type or "unknown_alert"),
        "severity": str(severity or "warning").strip().lower() or "warning",
        "message": str(message or "").strip() or "observability alert",
        "metadata": metadata or {},
    }

    path = observability_alert_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")

    metric_key = ALERT_METRIC_KEYS.get(entry["type"])
    if metric_key:
        control_plane_metrics.increment(metric_key)

    return {"emitted": True, "entry": entry}
