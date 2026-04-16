from __future__ import annotations

import threading
import time
from typing import Any, Dict


class _ControlPlaneMetrics:
    """Lightweight in-process counter store for control-plane observability."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._counters: Dict[str, float] = {
            "plan_execute_total": 0,
            "plan_execute_auto": 0,
            "plan_execute_approved": 0,
            "plan_execute_rejected": 0,
            "plan_execute_blocked": 0,
            "plan_execute_failed": 0,
            "blacklist_activations": 0,
            "incident_mode_activations": 0,
            "plan_snapshots_saved": 0,
            "edited_plan_executions": 0,
            "pruned_step_count": 0,
            "simulation_runs": 0,
            "pruned_predicted_failures": 0.0,
            "replaced_step_count": 0,
            "replacement_success_count": 0,
            "replacement_failure_count": 0,
            "outcome_events_total": 0,
            "real_outcome_events_total": 0,
            "alert_outcome_ingestion_failures": 0,
            "alert_score_drift_spikes": 0,
            "alert_memory_write_errors": 0,
        }

    def increment(self, key: str, by: float = 1) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + by

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._counters)

    def approval_rate(self) -> float | None:
        total = self._counters.get("plan_execute_total", 0)
        if total == 0:
            return None
        return round(self._counters.get("plan_execute_approved", 0) / total, 4)

    def auto_execution_rate(self) -> float | None:
        total = self._counters.get("plan_execute_total", 0)
        if total == 0:
            return None
        return round(self._counters.get("plan_execute_auto", 0) / total, 4)

    def simulation_usage_rate(self) -> float | None:
        simulations = self._counters.get("simulation_runs", 0)
        executions = self._counters.get("plan_execute_total", 0)
        denominator = simulations + executions
        if denominator == 0:
            return None
        return round(simulations / denominator, 4)

    def prune_effectiveness(self) -> float | None:
        pruned = self._counters.get("pruned_step_count", 0)
        if pruned == 0:
            return None
        predicted_failures = self._counters.get("pruned_predicted_failures", 0.0)
        return round(max(0.0, min(predicted_failures / pruned, 1.0)), 4)

    def replacement_rate(self) -> float | None:
        replaced = self._counters.get("replaced_step_count", 0)
        pruned = self._counters.get("pruned_step_count", 0)
        denominator = replaced + pruned
        if denominator == 0:
            return None
        return round(replaced / denominator, 4)

    def replacement_success_rate(self) -> float | None:
        replaced = self._counters.get("replaced_step_count", 0)
        if replaced == 0:
            return None
        successes = self._counters.get("replacement_success_count", 0)
        return round(max(0.0, min(successes / replaced, 1.0)), 4)

    def learning_signal_density(self) -> float:
        elapsed_hours = max((time.time() - self._started_at) / 3600.0, 1e-6)
        outcome_events = float(self._counters.get("outcome_events_total", 0) or 0)
        return round(outcome_events / elapsed_hours, 4)

    def real_signal_density(self) -> float:
        elapsed_hours = max((time.time() - self._started_at) / 3600.0, 1e-6)
        real_events = float(self._counters.get("real_outcome_events_total", 0) or 0)
        return round(real_events / elapsed_hours, 4)

    def to_dict(self) -> Dict[str, Any]:
        counters = self.snapshot()
        return {
            "counters": counters,
            "rates": {
                "approval_rate": self.approval_rate(),
                "auto_execution_rate": self.auto_execution_rate(),
                "simulation_usage_rate": self.simulation_usage_rate(),
                "prune_effectiveness": self.prune_effectiveness(),
                "replacement_rate": self.replacement_rate(),
                "replacement_success_rate": self.replacement_success_rate(),
                "learning_signal_density": self.learning_signal_density(),
                "real_signal_density": self.real_signal_density(),
            },
        }


control_plane_metrics = _ControlPlaneMetrics()
