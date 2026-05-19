"""
STEP 12D — Simulation Engine
==============================
Evaluates candidate execution paths before ANDIE commits to action.

This is *cognitive rehearsal* — ANDIE simulates what will likely happen if it
executes a task now, with preemptive adaptation, or not at all, and recommends
the highest-utility path.

Three candidate paths
---------------------
    AS_IS     — execute now without changes
    ADAPTED   — execute now after applying preemptive adaptations
    DEFERRED  — delay until memory/infrastructure conditions improve
    ABORTED   — do not execute; escalate to consensus/human

Path selection
--------------
Each path is scored by a ``utility()`` function:

    utility = success_probability × expected_confidence × (1 − cost)

The path with the highest utility is recommended, subject to a hard floor:
if the ``AS_IS`` success probability is < ``abort_threshold``, the engine
will not recommend ``AS_IS`` even if it scores highest (because cost=0 means
low-confidence paths still score high on AS_IS).

Preemptive adaptations (ADAPTED path)
--------------------------------------
Adaptations are collected from:
    - RiskAssessment.recommended_preemptive_actions
    - InfrastructureForecast.recommended_actions
    - Built-in rules for specific pressure levels

The ADAPTED path assigns a ``success_probability`` boost of ``_ADAPT_BOOST``
(default +0.25) relative to the AS_IS baseline, reflecting the expectation that
preemption reduces failure probability.

Usage
-----
    engine = SimulationEngine(risk_engine, forecast_engine)
    result = engine.simulate("deploy_api",
                              context_tags=["gpu_pressure"],
                              node_id="nuc-main",
                              task_id="wave-03-deploy")

    if not result.should_execute():
        # defer or abort
        ...
    for action in result.recommended_adaptations:
        apply(action)
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from .forecast_engine    import ForecastEngine
from .prediction_models  import (
    InfrastructureForecast, PressureLevel, RiskAssessment,
    SimulationPath, SimulationPathType, SimulationResult,
)
from .risk_engine        import RiskEngine

# Probability boost when preemptive adaptations are applied
_ADAPT_BOOST = 0.25

# If predicted success probability is below this even after adaptation, ABORT
_ABORT_THRESHOLD = 0.20

# Risk threshold for recommending deferral
_DEFER_THRESHOLD = 0.80

# Cost model (relative effort/delay per path type)
_PATH_COST = {
    SimulationPathType.AS_IS:    0.00,
    SimulationPathType.ADAPTED:  0.15,
    SimulationPathType.DEFERRED: 0.40,
    SimulationPathType.ABORTED:  1.00,
}

# Confidence adjustment when no node forecast is available
_NO_FORECAST_CONFIDENCE = 0.55


class SimulationEngine:
    """Simulates candidate execution paths and recommends the best one.

    Parameters
    ----------
    risk_engine:
        Pre-execution risk assessor.
    forecast_engine:
        Infrastructure pressure forecaster.
    adapt_boost:
        How much the ADAPTED path raises success probability.
    defer_threshold:
        Risk probability above which DEFERRED is considered.
    abort_threshold:
        Success probability floor; below this even ADAPTED → ABORTED.
    """

    def __init__(
        self,
        risk_engine:      RiskEngine,
        forecast_engine:  ForecastEngine,
        adapt_boost:      float = _ADAPT_BOOST,
        defer_threshold:  float = _DEFER_THRESHOLD,
        abort_threshold:  float = _ABORT_THRESHOLD,
    ) -> None:
        self._risk     = risk_engine
        self._forecast = forecast_engine
        self._boost    = adapt_boost
        self._defer    = defer_threshold
        self._abort    = abort_threshold

    # ── Public API ────────────────────────────────────────────────────────────

    def simulate(
        self,
        task:         str,
        context_tags: Optional[List[str]] = None,
        node_id:      Optional[str]       = None,
        agent_id:     Optional[str]       = None,
        task_id:      Optional[str]       = None,
    ) -> SimulationResult:
        """Simulate execution paths and return a recommended strategy."""
        tags = list(context_tags or [])
        tid  = task_id or f"sim-{uuid.uuid4().hex[:8]}"

        # ── 1. Risk assessment ────────────────────────────────────────────────
        assessment = self._risk.assess(
            task=task, context_tags=tags,
            node_id=node_id, agent_id=agent_id, task_id=tid,
        )

        # ── 2. Infrastructure forecast (optional) ─────────────────────────────
        forecast: Optional[InfrastructureForecast] = None
        if node_id:
            forecast = self._forecast.forecast(task, node_id, tags)

        # ── 3. Build candidate paths ──────────────────────────────────────────
        base_fail_p  = assessment.predicted_failure_probability
        base_success = round(1.0 - base_fail_p, 4)
        base_conf    = assessment.confidence

        # Collect all adaptations from risk + forecast
        adaptations: List[str] = list(assessment.recommended_preemptive_actions)
        if forecast:
            for a in forecast.recommended_actions:
                if a not in adaptations:
                    adaptations.append(a)

        # Adjusted success probability for ADAPTED path
        adapted_success = round(min(base_success + self._boost, 0.95), 4)
        # Confidence boost from having a richer set of adaptations
        adapt_conf      = round(min(base_conf * 1.1, 0.95), 4)

        paths: List[SimulationPath] = []

        # AS_IS
        paths.append(SimulationPath(
            path_type=SimulationPathType.AS_IS,
            success_probability=base_success,
            expected_confidence=base_conf,
            adaptations=[],
            cost=_PATH_COST[SimulationPathType.AS_IS],
            notes="Execute immediately without changes.",
        ))

        # ADAPTED
        paths.append(SimulationPath(
            path_type=SimulationPathType.ADAPTED,
            success_probability=adapted_success,
            expected_confidence=adapt_conf,
            adaptations=adaptations[:6],
            cost=_PATH_COST[SimulationPathType.ADAPTED],
            notes="Apply preemptive actions then execute.",
        ))

        # DEFERRED (only when risk is very high)
        if base_fail_p >= self._defer:
            defer_success = round(min(adapted_success + 0.15, 0.90), 4)
            paths.append(SimulationPath(
                path_type=SimulationPathType.DEFERRED,
                success_probability=defer_success,
                expected_confidence=round(adapt_conf * 0.85, 4),
                adaptations=["wait_for_pressure_to_drop", "monitor_node_health"],
                cost=_PATH_COST[SimulationPathType.DEFERRED],
                notes="Delay until infrastructure conditions improve.",
            ))

        # ABORTED (only when adaptation still leaves low success)
        if adapted_success < self._abort:
            paths.append(SimulationPath(
                path_type=SimulationPathType.ABORTED,
                success_probability=0.0,
                expected_confidence=1.0,
                adaptations=["escalate_to_consensus", "alert_sentinel"],
                cost=_PATH_COST[SimulationPathType.ABORTED],
                notes="Do not execute; escalate for human/consensus review.",
            ))

        # ── 4. Select best path ───────────────────────────────────────────────
        best_path, rationale = self._select(paths, base_fail_p, adapted_success)

        # ── 5. Assemble result ────────────────────────────────────────────────
        rec_adaptations = (
            best_path.adaptations
            if best_path.path_type == SimulationPathType.ADAPTED
            else []
        )
        overall_conf = forecast.confidence if forecast else (base_conf or _NO_FORECAST_CONFIDENCE)

        return SimulationResult(
            task_id=tid,
            task=task,
            node_id=node_id,
            risk_assessment=assessment,
            infra_forecast=forecast,
            paths=paths,
            recommended_path=best_path.path_type,
            recommended_adaptations=rec_adaptations,
            overall_confidence=round(overall_conf, 4),
            rationale=rationale,
        )

    # ── Selection logic ───────────────────────────────────────────────────────

    def _select(
        self,
        paths:         List[SimulationPath],
        base_fail_p:   float,
        adapted_success: float,
    ) -> tuple[SimulationPath, str]:
        """Pick the highest-utility path, with safety guardrails."""
        # Hard rule: if even adaptation leaves < abort_threshold success, abort
        abort_paths = [p for p in paths if p.path_type == SimulationPathType.ABORTED]
        if adapted_success < self._abort and abort_paths:
            return abort_paths[0], (
                f"Even with adaptations, success probability ({adapted_success:.2f}) "
                f"is below abort floor ({self._abort:.2f}). Escalating."
            )

        # Sort by utility descending
        ranked = sorted(paths, key=lambda p: p.utility(), reverse=True)
        best   = ranked[0]

        # Build rationale
        rationale = (
            f"Risk={base_fail_p:.2f} → "
            f"recommended '{best.path_type.value}' "
            f"(utility={best.utility():.3f}, "
            f"success_p={best.success_probability:.2f})"
        )
        if best.adaptations:
            rationale += f"; adaptations: {best.adaptations[:3]}"

        return best, rationale
