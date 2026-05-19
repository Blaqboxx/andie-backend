"""
Resource Monitor — collects real-time host resource snapshots.

Primary source: psutil (CPU, RAM, disk, network).
GPU source:     pynvml (NVIDIA) — gracefully degrades to 0.0 % when
                the library or hardware is unavailable.

If psutil itself is missing the monitor returns a zeroed snapshot so the
rest of the cognition stack continues without hard errors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from .resource_models import ResourceSnapshot

log = logging.getLogger(__name__)

# ── Optional dependencies ───────────────────────────────────────────────
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False
    log.warning(
        "psutil not installed — ResourceMonitor will return synthetic zeros. "
        "Install with: pip install psutil"
    )

try:
    import pynvml  # type: ignore[import]
    pynvml.nvmlInit()
    _NVML_AVAILABLE = True
except Exception:
    pynvml = None  # type: ignore[assignment]
    _NVML_AVAILABLE = False


# ── GPU helper ──────────────────────────────────────────────────────────

def _gpu_percent() -> float:
    """Return GPU utilization [0–100] for the first device, or 0.0."""
    if not _NVML_AVAILABLE:
        return 0.0
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util   = pynvml.nvmlDeviceGetUtilizationRates(handle)
        return float(util.gpu)
    except Exception:
        return 0.0


# ── Monitor ─────────────────────────────────────────────────────────────

class ResourceMonitor:
    """
    Collects instantaneous and continuous resource snapshots.

    Usage::

        monitor = ResourceMonitor()
        snap    = monitor.snapshot()
        print(snap.cpu_percent, snap.ram_percent)

    For continuous polling::

        await monitor.start_polling(interval=2.0, on_snapshot=my_callback)
    """

    def __init__(self) -> None:
        self._latest: Optional[ResourceSnapshot] = None

    # ------------------------------------------------------------------ #
    # Synchronous snapshot                                                 #
    # ------------------------------------------------------------------ #

    def snapshot(self, active_tasks: int = 0) -> ResourceSnapshot:
        """
        Take a synchronous point-in-time reading.

        Uses a 0.1 s CPU measurement window (non-blocking for most purposes).
        Falls back to zeroed snapshot when psutil is unavailable.
        """
        if not _PSUTIL_AVAILABLE:
            snap = ResourceSnapshot(active_tasks=active_tasks)
            self._latest = snap
            return snap

        vm   = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net  = psutil.net_io_counters()

        snap = ResourceSnapshot(
            cpu_percent  = psutil.cpu_percent(interval=0.1),
            ram_percent  = vm.percent,
            gpu_percent  = _gpu_percent(),
            disk_percent = disk.percent,
            active_tasks = active_tasks,
            net_sent_mb  = round(net.bytes_sent / 1_048_576, 2),
            net_recv_mb  = round(net.bytes_recv / 1_048_576, 2),
        )
        self._latest = snap
        return snap

    def latest(self) -> Optional[ResourceSnapshot]:
        """Return the most recently cached snapshot, or None."""
        return self._latest

    # ------------------------------------------------------------------ #
    # Continuous async polling                                             #
    # ------------------------------------------------------------------ #

    async def start_polling(
        self,
        interval: float = 2.0,
        active_tasks_fn: Optional[Callable[[], int]] = None,
        on_snapshot: Optional[Callable[[ResourceSnapshot], None]] = None,
    ) -> None:
        """
        Coroutine that polls in a loop — wrap in ``asyncio.ensure_future``.

        Parameters
        ----------
        interval:
            Seconds between readings.
        active_tasks_fn:
            Zero-argument callable returning the current running-task count.
        on_snapshot:
            Callback invoked with every new ResourceSnapshot.
        """
        while True:
            try:
                count = active_tasks_fn() if active_tasks_fn else 0
                snap  = self.snapshot(active_tasks=count)
                if on_snapshot:
                    on_snapshot(snap)
            except Exception as exc:
                log.debug("ResourceMonitor poll error: %s", exc)
            await asyncio.sleep(interval)
