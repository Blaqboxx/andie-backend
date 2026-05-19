"""
Capacity Engine — converts a ResourceSnapshot into a SchedulingDecision.

Pressure classification per metric:

    value ≥ critical        → CRITICAL
    value ≥ warning         → HIGH
    value ≥ warning × 0.85  → MODERATE
    value ≥ warning × 0.70  → LOW
    otherwise               → NONE

Overall pressure = max(cpu, ram, gpu, disk) by severity.

Parallelism reduction formula:

    NONE     → scale 1.00  (no change)
    LOW      → scale 0.75
    MODERATE → scale 0.50
    HIGH     → scale 0.25
    CRITICAL → clamp to 1

LLM postponement is triggered when GPU ≥ HIGH or RAM = CRITICAL because
LLM inference is the primary consumer of both resources simultaneously.
"""

from __future__ import annotations

import math
from typing import List

from .resource_models import (
    PressureLevel,
    ResourceSnapshot,
    ResourceThresholds,
    SchedulingDecision,
)

# ── Severity ordering ───────────────────────────────────────────────────

_LEVEL_ORDER: List[PressureLevel] = [
    PressureLevel.NONE,
    PressureLevel.LOW,
    PressureLevel.MODERATE,
    PressureLevel.HIGH,
    PressureLevel.CRITICAL,
]

_SCALE: dict[PressureLevel, float] = {
    PressureLevel.NONE:     1.00,
    PressureLevel.LOW:      0.75,
    PressureLevel.MODERATE: 0.50,
    PressureLevel.HIGH:     0.25,
    PressureLevel.CRITICAL: 0.00,   # clamped to 1 in assess()
}


def _classify(value: float, warn: float, crit: float) -> PressureLevel:
    """Map a single metric reading to a PressureLevel."""
    if value >= crit:
        return PressureLevel.CRITICAL
    if value >= warn:
        return PressureLevel.HIGH
    if value >= warn * 0.85:
        return PressureLevel.MODERATE
    if value >= warn * 0.70:
        return PressureLevel.LOW
    return PressureLevel.NONE


def _max_level(*levels: PressureLevel) -> PressureLevel:
    return max(levels, key=lambda lv: _LEVEL_ORDER.index(lv))


# ── Engine ──────────────────────────────────────────────────────────────

class CapacityEngine:
    """
    Stateless engine — call ``assess()`` per wave to get a SchedulingDecision.

    Usage::

        engine   = CapacityEngine()
        decision = engine.assess(snapshot, requested_parallelism=4)
        if decision.should_postpone_llm:
            ...
    """

    def __init__(self, thresholds: ResourceThresholds | None = None) -> None:
        self._t = thresholds or ResourceThresholds()

    def assess(
        self,
        snapshot: ResourceSnapshot,
        requested_parallelism: int = 4,
    ) -> SchedulingDecision:
        """
        Evaluate snapshot and return a SchedulingDecision.

        Parameters
        ----------
        snapshot:
            Current resource reading from ResourceMonitor.
        requested_parallelism:
            The caller's preferred max-concurrent-tasks count.

        Returns
        -------
        SchedulingDecision with adjusted parallelism, pressure level,
        postpone flag, and human-readable action list.
        """
        t = self._t
        sources: List[str] = []
        actions: List[str] = []

        # ── Per-metric classification ─────────────────────────────────
        cpu_lv  = _classify(snapshot.cpu_percent,  t.cpu_warning,  t.cpu_critical)
        ram_lv  = _classify(snapshot.ram_percent,  t.ram_warning,  t.ram_critical)
        gpu_lv  = _classify(snapshot.gpu_percent,  t.gpu_warning,  t.gpu_critical)
        disk_lv = _classify(snapshot.disk_percent, t.disk_warning, t.disk_critical)

        overall = _max_level(cpu_lv, ram_lv, gpu_lv, disk_lv)

        if cpu_lv  != PressureLevel.NONE:
            sources.append(f"cpu={snapshot.cpu_percent:.1f}% [{cpu_lv.value}]")
        if ram_lv  != PressureLevel.NONE:
            sources.append(f"ram={snapshot.ram_percent:.1f}% [{ram_lv.value}]")
        if gpu_lv  != PressureLevel.NONE:
            sources.append(f"gpu={snapshot.gpu_percent:.1f}% [{gpu_lv.value}]")
        if disk_lv != PressureLevel.NONE:
            sources.append(f"disk={snapshot.disk_percent:.1f}% [{disk_lv.value}]")

        # ── Parallelism reduction ─────────────────────────────────────
        scale = _SCALE[overall]
        recommended = 1 if scale == 0.0 else max(1, math.floor(requested_parallelism * scale))

        # ── LLM postponement decision ─────────────────────────────────
        gpu_heavy = gpu_lv in (PressureLevel.HIGH, PressureLevel.CRITICAL)
        ram_heavy = ram_lv == PressureLevel.CRITICAL
        should_postpone_llm = gpu_heavy or ram_heavy

        postpone_reason = ""
        if should_postpone_llm:
            parts = []
            if gpu_heavy:
                parts.append(f"GPU at {snapshot.gpu_percent:.1f}%")
            if ram_heavy:
                parts.append(f"RAM at {snapshot.ram_percent:.1f}%")
            postpone_reason = "Postponing LLM tasks — " + ", ".join(parts)

        # ── Action list ───────────────────────────────────────────────
        if recommended < requested_parallelism:
            actions.append(f"reduce_parallelism:{requested_parallelism}→{recommended}")
        if should_postpone_llm:
            actions.append("postpone_llm_tasks")
        if overall == PressureLevel.CRITICAL:
            actions.append("alert_operator")

        return SchedulingDecision(
            recommended_parallelism = recommended,
            pressure_level          = overall,
            pressure_sources        = sources,
            should_postpone_llm     = should_postpone_llm,
            postpone_reason         = postpone_reason,
            actions                 = actions,
            snapshot                = snapshot,
        )
