from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .controller import ExecutiveController


@dataclass
class SchedulerState:
    enabled: bool = False
    interval_seconds: int = 60
    last_run_at: datetime | None = None
    cycles_completed: int = 0
    halt_reason: str | None = None
    halted_at: datetime | None = None


class BoundedScheduler:
    def __init__(
        self,
        controller: ExecutiveController,
        *,
        interval_seconds: int = 60,
        now_provider: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.controller = controller
        self.state = SchedulerState(interval_seconds=max(1, int(interval_seconds)))
        self._now = now_provider or (lambda: datetime.now(timezone.utc))

    def start(self) -> SchedulerState:
        self.state.enabled = True
        self.state.halt_reason = None
        self.state.halted_at = None
        return self.state

    def halt(self, reason: str) -> SchedulerState:
        self.state.enabled = False
        self.state.halt_reason = str(reason)
        self.state.halted_at = self._now()
        return self.state

    def can_run(self) -> bool:
        if not self.state.enabled:
            return False

        slo = self.controller.get_operational_slo_snapshot()
        policy_violation_rate = (
            slo.get('metrics', {})
            .get('governance', {})
            .get('policy_violation_rate', {})
            .get('value', 0.0)
        )
        if float(policy_violation_rate) > 0.0:
            self.halt('policy_violation_rate')
            return False

        if self.controller.budget_breach():
            self.halt('budget_breach')
            return False

        if self.controller.stale_intent_threshold_exceeded():
            self.halt('stale_intent_threshold')
            return False

        return True

    def run_once(self) -> Dict[str, Any]:
        if not self.state.enabled:
            return {
                'status': 'skipped',
                'reason': 'scheduler_disabled',
                'state': self._state_dict(),
            }

        if not self.can_run():
            return {
                'status': 'halted',
                'reason': self.state.halt_reason,
                'state': self._state_dict(),
            }

        outcome = self.controller.run_cycle()
        self.state.cycles_completed = int(self.state.cycles_completed) + 1
        self.state.last_run_at = self._now()
        return {
            'status': 'ran',
            'outcome': outcome,
            'state': self._state_dict(),
        }

    def _state_dict(self) -> Dict[str, Any]:
        return {
            'enabled': bool(self.state.enabled),
            'interval_seconds': int(self.state.interval_seconds),
            'last_run_at': self.state.last_run_at.isoformat() if self.state.last_run_at else None,
            'cycles_completed': int(self.state.cycles_completed),
            'halt_reason': self.state.halt_reason,
            'halted_at': self.state.halted_at.isoformat() if self.state.halted_at else None,
        }
