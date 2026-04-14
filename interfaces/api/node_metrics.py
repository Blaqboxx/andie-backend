from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

import psutil


def system_metrics(role: str) -> Dict[str, Any]:
    load_average = psutil.getloadavg()[0] if hasattr(psutil, "getloadavg") else 0.0
    cpu_count = max(psutil.cpu_count() or 1, 1)
    memory = psutil.virtual_memory()
    cpu_percent = psutil.cpu_percent(interval=None)
    return {
        "cpuPercent": round(cpu_percent, 2),
        "memoryUsedPercent": round(memory.percent, 2),
        "loadPerCpu": round(load_average / cpu_count, 2),
        "cpuCount": cpu_count,
        "loadAverage": round(load_average, 2),
        "role": role,
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    }