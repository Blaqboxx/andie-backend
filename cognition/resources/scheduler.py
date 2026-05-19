"""
Resource-Aware Scheduler — STEP 8 orchestration surface.

ResourceAwareScheduler is the single facade consumed by TaskGraph and the
build pipeline.  Before each execution wave it:

  1. Collects a fresh ResourceSnapshot (off the event loop via run_in_executor)
  2. Feeds it to CapacityEngine.assess()
  3. Returns an adjusted max_parallel count
  4. Optionally emits a ``resource_pressure`` event to the graph's event bus
  5. Flags whether LLM-heavy tasks should be held back

Usage inside TaskGraph::

    scheduler = ResourceAwareScheduler()

    # Per-wave capacity check
    parallelism = await scheduler.calculate_capacity(requested=4, active_tasks=2)

    # LLM gate
    if scheduler.should_postpone_llm():
        skip_llm_nodes()

    # Emit to event bus
    scheduler.emit_pressure_event(event_cb, scheduler.latest_decision())

Usage with continuous background monitoring::

    scheduler.start_background_polling(event_cb=on_event)
    # ... do work ...
    scheduler.stop_background_polling()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from .capacity_engine import CapacityEngine
from .resource_models import (
    PressureLevel,
    ResourceSnapshot,
    ResourceThresholds,
    SchedulingDecision,
)
from .resource_monitor import ResourceMonitor

log = logging.getLogger(__name__)


class ResourceAwareScheduler:
    """
    High-level scheduling facade.

    Combines ResourceMonitor + CapacityEngine into a single callable surface.
    Thread-safe for reads; snapshot collection is async-friendly via
    run_in_executor so the event loop is never blocked.
    """

    def __init__(
        self,
        thresholds: ResourceThresholds | None = None,
        polling_interval: float = 2.0,
    ) -> None:
        self._monitor  = ResourceMonitor()
        self._engine   = CapacityEngine(thresholds)
        self._interval = polling_interval
        self._latest_decision: Optional[SchedulingDecision] = None
        self._poll_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ #
    # Core API                                                             #
    # ------------------------------------------------------------------ #

    def snapshot(self, active_tasks: int = 0) -> ResourceSnapshot:
        """Synchronous snapshot — safe to call from non-async code."""
        return self._monitor.snapshot(active_tasks=active_tasks)

    def assess(
        self,
        requested_parallelism: int = 4,
        active_tasks: int = 0,
    ) -> SchedulingDecision:
        """
        Collect a fresh snapshot synchronously and return a SchedulingDecision.

        Suitable for pre-wave checks where an event loop may not be running.
        """
        snap     = self._monitor.snapshot(active_tasks=active_tasks)
        decision = self._engine.assess(snap, requested_parallelism)
        self._latest_decision = decision
        return decision

    async def calculate_capacity(
        self,
        requested: int = 4,
        active_tasks: int = 0,
    ) -> int:
        """
        Async capacity check — runs snapshot collection off the event loop.

        Returns the recommended max_parallel count for the next wave.
        Always ≥ 1.
        """
        loop = asyncio.get_event_loop()
        snap = await loop.run_in_executor(
            None, lambda: self._monitor.snapshot(active_tasks=active_tasks)
        )
        decision = self._engine.assess(snap, requested)
        self._latest_decision = decision
        return decision.recommended_parallelism

    def should_postpone_llm(self) -> bool:
        """True if the latest assessment recommends holding LLM-heavy tasks."""
        return bool(self._latest_decision and self._latest_decision.should_postpone_llm)

    def latest_decision(self) -> Optional[SchedulingDecision]:
        """Return the most recent SchedulingDecision, or None if never assessed."""
        return self._latest_decision

    # ------------------------------------------------------------------ #
    # Event bus integration                                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def emit_pressure_event(
        event_cb: Callable[[dict], None],
        decision: SchedulingDecision,
    ) -> None:
        """
        Push a ``resource_pressure`` event to the graph event bus.

        No-ops silently when pressure level is NONE (no noise on healthy hosts).

        The emitted payload::

            {
              "event":                  "resource_pressure",
              "pressure_level":         "high",
              "pressure_sources":       ["gpu=91.0% [critical]"],
              "recommended_parallelism": 1,
              "should_postpone_llm":    true,
              "postpone_reason":        "...",
              "actions":                ["reduce_parallelism:4→1", "postpone_llm_tasks"],
              "snapshot":               { ... }
            }
        """
        if decision.pressure_level == PressureLevel.NONE:
            return

        snap = decision.snapshot
        event_cb(
            {
                "event":                   "resource_pressure",
                "pressure_level":          decision.pressure_level.value,
                "pressure_sources":        decision.pressure_sources,
                "recommended_parallelism": decision.recommended_parallelism,
                "should_postpone_llm":     decision.should_postpone_llm,
                "postpone_reason":         decision.postpone_reason,
                "actions":                 decision.actions,
                "snapshot":                snap.to_dict() if snap else None,
            }
        )
        log.info(
            "Resource pressure [%s]: sources=%s actions=%s",
            decision.pressure_level.value,
            decision.pressure_sources,
            decision.actions,
        )

    # ------------------------------------------------------------------ #
    # Background polling                                                   #
    # ------------------------------------------------------------------ #

    def start_background_polling(
        self,
        event_cb: Optional[Callable[[dict], None]] = None,
        active_tasks_fn: Optional[Callable[[], int]] = None,
    ) -> None:
        """
        Launch continuous polling as an asyncio background task.

        Must be called from inside a running event loop (e.g., a FastAPI
        lifespan handler or an ``async with`` block).

        Parameters
        ----------
        event_cb:
            Optional callback for resource_pressure events.
        active_tasks_fn:
            Zero-arg callable returning the current running-task count.
        """
        async def _poll_loop() -> None:
            while True:
                try:
                    count    = active_tasks_fn() if active_tasks_fn else 0
                    snap     = self._monitor.snapshot(active_tasks=count)
                    # Re-assess using the last recommended parallelism as the
                    # "requested" baseline so pressure continues to compound.
                    prev     = self._latest_decision
                    base     = prev.recommended_parallelism if prev else 4
                    decision = self._engine.assess(snap, base)
                    self._latest_decision = decision
                    if event_cb:
                        self.emit_pressure_event(event_cb, decision)
                except Exception as exc:
                    log.debug("Scheduler poll error: %s", exc)
                await asyncio.sleep(self._interval)

        self._poll_task = asyncio.ensure_future(_poll_loop())
        log.info("ResourceAwareScheduler background polling started (interval=%.1fs)", self._interval)

    def stop_background_polling(self) -> None:
        """Cancel the background polling task if running."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None
            log.info("ResourceAwareScheduler background polling stopped.")
