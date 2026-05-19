"""
cognition.prediction — STEP 12 Predictive Cognitive Planning
=============================================================
Transforms ANDIE from reactive intelligence into anticipatory intelligence.

Before STEP 12:
    failure occurs → recover

After STEP 12:
    predict likely failure → avoid failure entirely

Modules
-------
prediction_models  — shared Pydantic v2 contracts
risk_engine        — pre-execution risk assessment (episodic + semantic + node)
forecast_engine    — infrastructure pressure forecasting
simulation_engine  — cognitive rehearsal (compare execution paths)
trajectory_analyzer — operational trend detection

Primary entry-points
--------------------
    RiskEngine      — assess(task, context_tags, node_id) → RiskAssessment
    ForecastEngine  — forecast(task, node_id, context_tags) → InfrastructureForecast
    SimulationEngine — simulate(task, context_tags, node_id) → SimulationResult
    TrajectoryAnalyzer — analyze_task/node/agent() → TrajectoryReport

Quick start
-----------
    from cognition.memory import MemoryRetriever
    from cognition.prediction import RiskEngine, ForecastEngine, SimulationEngine, TrajectoryAnalyzer

    memory   = MemoryRetriever.from_dir("/var/andie/memory")
    risk     = RiskEngine(memory)
    forecast = ForecastEngine(memory)
    sim      = SimulationEngine(risk, forecast)
    traj     = TrajectoryAnalyzer(memory)

    # Before executing a wave:
    result = sim.simulate("deploy_api", context_tags=["gpu_pressure"], node_id="nuc-main")
    if result.should_execute():
        for action in result.recommended_adaptations:
            apply_preemptive(action)
        dispatch(task)

    # Continuous monitoring:
    report = traj.analyze_node("nuc-main")
    if report.direction == TrendDirection.DECLINING:
        alert(report.alert)
"""

from .forecast_engine     import ForecastEngine
from .prediction_models   import (
    DataPoint,
    InfrastructureForecast,
    PressureLevel,
    RiskAssessment,
    RiskFactor,
    SimulationPath,
    SimulationPathType,
    SimulationResult,
    TrajectoryReport,
    TrendDirection,
)
from .risk_engine         import RiskEngine
from .simulation_engine   import SimulationEngine
from .trajectory_analyzer import TrajectoryAnalyzer

__all__ = [
    # Engines
    "RiskEngine",
    "ForecastEngine",
    "SimulationEngine",
    "TrajectoryAnalyzer",
    # Models
    "RiskFactor",
    "RiskAssessment",
    "PressureLevel",
    "InfrastructureForecast",
    "SimulationPath",
    "SimulationPathType",
    "SimulationResult",
    "DataPoint",
    "TrendDirection",
    "TrajectoryReport",
]
