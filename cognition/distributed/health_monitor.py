"""
Health Monitor — continuous polling of cluster nodes for STEP 9.

HealthMonitor polls every registered node on a configurable interval,
updates the NodeRegistry with live metrics, and emits events to the
graph event bus when node health changes state:

  * ``node_degraded``  — node crossed a pressure threshold
  * ``node_recovered`` — previously degraded/offline node is healthy again
  * ``node_offline``   — node is unreachable
  * ``node_online``    — newly reachable node

Probe strategies (in priority order):
  1. Local node — psutil (zero network overhead)
  2. Remote node with ANDIE health API — HTTP GET /health (fast, rich)
  3. Remote node fallback — TCP socket ping (latency only, no metrics)

The monitor degrades gracefully: if psutil is missing, local metrics are
synthesised as 0.0 so the rest of the cognition stack continues unimpeded.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .node_models import (
    NodeCapability,
    NodeHealthReport,
    NodeState,
    NodeStatus,
)
from .node_registry import NodeRegistry

log = logging.getLogger(__name__)

# Pressure thresholds that mark a node DEGRADED (overrideable)
_DEFAULT_DEGRADED_CPU  = 85.0
_DEFAULT_DEGRADED_RAM  = 85.0
_DEFAULT_DEGRADED_GPU  = 90.0

EventCallback = Callable[[Dict[str, Any]], Any]

# ── Optional dependencies ───────────────────────────────────────────────
try:
    import psutil as _psutil
    _PSUTIL_OK = True
except ImportError:
    _psutil = None  # type: ignore[assignment]
    _PSUTIL_OK = False

try:
    import httpx as _httpx
    _HTTPX_OK = True
except ImportError:
    _httpx = None  # type: ignore[assignment]
    _HTTPX_OK = False


# ── Probe helpers ───────────────────────────────────────────────────────

def _probe_local() -> NodeHealthReport:
    """Collect local node metrics via psutil."""
    if not _PSUTIL_OK:
        return NodeHealthReport(node_id="local", reachable=True)

    try:
        vm   = _psutil.virtual_memory()
        disk = _psutil.disk_usage("/")
        cpu  = _psutil.cpu_percent(interval=0.1)
        return NodeHealthReport(
            node_id="local",
            reachable=True,
            cpu=cpu,
            ram=vm.percent,
            gpu=0.0,
            disk=disk.percent,
        )
    except Exception as exc:
        log.debug("Local probe error: %s", exc)
        return NodeHealthReport(node_id="local", reachable=True)


async def _probe_http(node: NodeState, timeout: float = 3.0) -> NodeHealthReport:
    """
    Try GET http://{hostname}:{port}/health — expects JSON with keys
    cpu_percent, ram_percent, gpu_percent, disk_percent.
    Falls back to TCP ping on failure.
    """
    t0 = time.perf_counter()
    if _HTTPX_OK:
        try:
            url = f"http://{node.hostname}:{node.port}/health"
            async with _httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
                latency = (time.perf_counter() - t0) * 1000
                if resp.status_code == 200:
                    data = resp.json()
                    return NodeHealthReport(
                        node_id=node.node_id,
                        reachable=True,
                        cpu=float(data.get("cpu_percent", 0.0)),
                        ram=float(data.get("ram_percent", 0.0)),
                        gpu=float(data.get("gpu_percent", 0.0)),
                        disk=float(data.get("disk_percent", 0.0)),
                        latency_ms=round(latency, 2),
                    )
        except Exception:
            pass

    # Fallback: TCP ping to SSH port
    return await _probe_tcp(node, timeout=timeout)


async def _probe_tcp(node: NodeState, timeout: float = 2.0) -> NodeHealthReport:
    """Minimal reachability check via asyncio TCP connect."""
    t0 = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(node.hostname, node.port),
            timeout=timeout,
        )
        writer.close()
        latency = (time.perf_counter() - t0) * 1000
        return NodeHealthReport(
            node_id=node.node_id,
            reachable=True,
            latency_ms=round(latency, 2),
        )
    except Exception as exc:
        return NodeHealthReport(
            node_id=node.node_id,
            reachable=False,
            error=str(exc),
        )


# ── Monitor ─────────────────────────────────────────────────────────────

class HealthMonitor:
    """
    Continuous node health polling with event-bus integration.

    Usage::

        registry = NodeRegistry()
        monitor  = HealthMonitor(registry, interval=10.0)

        # Single-shot check
        reports = await monitor.check_all()

        # Continuous background task
        monitor.start(event_cb=on_event)
        # ... later ...
        monitor.stop()
    """

    def __init__(
        self,
        registry: NodeRegistry,
        *,
        interval: float = 10.0,
        degraded_cpu: float = _DEFAULT_DEGRADED_CPU,
        degraded_ram: float = _DEFAULT_DEGRADED_RAM,
        degraded_gpu: float = _DEFAULT_DEGRADED_GPU,
    ) -> None:
        self._registry     = registry
        self._interval     = interval
        self._degraded_cpu = degraded_cpu
        self._degraded_ram = degraded_ram
        self._degraded_gpu = degraded_gpu
        self._poll_task: Optional[asyncio.Task] = None
        # Track previous status to emit transition events
        self._prev_status: Dict[str, NodeStatus] = {}

    # ------------------------------------------------------------------ #
    # Single-shot check                                                    #
    # ------------------------------------------------------------------ #

    async def check_node(self, node: NodeState) -> NodeHealthReport:
        """Probe one node and update the registry."""
        if node.node_id == "local":
            report = _probe_local()
        else:
            report = await _probe_http(node)

        updated = self._registry.apply_health_report(report)
        if updated and self._is_degraded(updated):
            updated.status = NodeStatus.DEGRADED
        return report

    async def check_all(self) -> List[NodeHealthReport]:
        """Probe every registered node concurrently."""
        nodes   = self._registry.all_nodes()
        reports = await asyncio.gather(
            *[self.check_node(n) for n in nodes],
            return_exceptions=False,
        )
        return list(reports)

    # ------------------------------------------------------------------ #
    # Background polling                                                   #
    # ------------------------------------------------------------------ #

    def start(self, event_cb: Optional[EventCallback] = None) -> None:
        """Launch background polling task (must be inside a running event loop)."""
        async def _loop() -> None:
            while True:
                try:
                    reports = await self.check_all()
                    if event_cb:
                        for report in reports:
                            await self._maybe_emit(report, event_cb)
                except Exception as exc:
                    log.debug("HealthMonitor poll error: %s", exc)
                await asyncio.sleep(self._interval)

        self._poll_task = asyncio.ensure_future(_loop())
        log.info("HealthMonitor started (interval=%.1fs, nodes=%d)", self._interval, len(self._registry))

    def stop(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None

    # ------------------------------------------------------------------ #
    # Event emission                                                       #
    # ------------------------------------------------------------------ #

    async def _maybe_emit(
        self,
        report: NodeHealthReport,
        event_cb: EventCallback,
    ) -> None:
        """Emit a state-transition event when a node's status changes."""
        node = self._registry.get(report.node_id)
        if node is None:
            return

        prev   = self._prev_status.get(node.node_id, NodeStatus.UNKNOWN)
        current = node.status

        if prev == current:
            self._prev_status[node.node_id] = current
            return

        self._prev_status[node.node_id] = current

        payload: Dict[str, Any] = {
            "node_id":   node.node_id,
            "hostname":  node.hostname,
            "cpu":       node.cpu_percent,
            "ram":       node.ram_percent,
            "gpu":       node.gpu_percent,
            "prev":      prev.value,
            "current":   current.value,
        }

        if current == NodeStatus.OFFLINE:
            payload["event"] = "node_offline"
            payload["error"] = report.error
        elif current == NodeStatus.DEGRADED:
            payload["event"] = "node_degraded"
            payload["pressure_sources"] = self._pressure_sources(node)
        elif current == NodeStatus.ONLINE and prev in (NodeStatus.DEGRADED, NodeStatus.OFFLINE):
            payload["event"] = "node_recovered"
        else:
            payload["event"] = "node_online"

        result = event_cb(payload)
        if asyncio.iscoroutine(result):
            await result

        log.info("HealthMonitor: [%s] %s → %s", node.node_id, prev.value, current.value)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _is_degraded(self, node: NodeState) -> bool:
        return (
            node.cpu_percent >= self._degraded_cpu
            or node.ram_percent >= self._degraded_ram
            or node.gpu_percent >= self._degraded_gpu
        )

    def _pressure_sources(self, node: NodeState) -> List[str]:
        sources = []
        if node.cpu_percent >= self._degraded_cpu:
            sources.append(f"cpu={node.cpu_percent:.1f}%")
        if node.ram_percent >= self._degraded_ram:
            sources.append(f"ram={node.ram_percent:.1f}%")
        if node.gpu_percent >= self._degraded_gpu:
            sources.append(f"gpu={node.gpu_percent:.1f}%")
        return sources
