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
        self._history_limit = 200
        self._history: list[Dict[str, Any]] = []
        self._halt_reasons: Dict[str, int] = {}

    def start(self) -> SchedulerState:
        self.state.enabled = True
        self.state.halt_reason = None
        self.state.halted_at = None
        return self.state

    def halt(self, reason: str) -> SchedulerState:
        self.state.enabled = False
        self.state.halt_reason = str(reason)
        self.state.halted_at = self._now()
        self._halt_reasons[self.state.halt_reason] = int(self._halt_reasons.get(self.state.halt_reason, 0)) + 1
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
            result = {
                'status': 'skipped',
                'reason': 'scheduler_disabled',
                'state': self._state_dict(),
            }
            self._append_history(result)
            return result

        if not self.can_run():
            result = {
                'status': 'halted',
                'reason': self.state.halt_reason,
                'state': self._state_dict(),
            }
            self._append_history(result)
            return result

        outcome = self.controller.run_cycle()
        self.state.cycles_completed = int(self.state.cycles_completed) + 1
        self.state.last_run_at = self._now()
        result = {
            'status': 'ran',
            'outcome': outcome,
            'state': self._state_dict(),
        }
        self._append_history(result)
        return result

    def run_cycles(self, cycles: int) -> Dict[str, Any]:
        requested_cycles = max(1, int(cycles))
        executed_cycles = 0
        last_result: Dict[str, Any] | None = None

        for _ in range(requested_cycles):
            last_result = self.run_once()
            if str(last_result.get('status')) != 'ran':
                break
            executed_cycles += 1

        if last_result is None:
            last_result = {
                'status': 'skipped',
                'reason': 'scheduler_disabled',
                'state': self._state_dict(),
            }

        return {
            'status': str(last_result.get('status', 'unknown')),
            'reason': last_result.get('reason'),
            'requested_cycles': requested_cycles,
            'executed_cycles': executed_cycles,
            'state': self._state_dict(),
        }

    def run_until_halt(self, max_cycles: int = 100) -> Dict[str, Any]:
        cycle_limit = max(1, int(max_cycles))
        executed_cycles = 0
        last_result: Dict[str, Any] | None = None

        while executed_cycles < cycle_limit:
            last_result = self.run_once()
            if str(last_result.get('status')) != 'ran':
                break
            executed_cycles += 1

        if last_result is None:
            last_result = {
                'status': 'skipped',
                'reason': 'scheduler_disabled',
                'state': self._state_dict(),
            }

        if str(last_result.get('status')) == 'ran' and executed_cycles >= cycle_limit:
            return {
                'status': 'max_cycles_reached',
                'reason': None,
                'max_cycles': cycle_limit,
                'executed_cycles': executed_cycles,
                'state': self._state_dict(),
            }

        return {
            'status': str(last_result.get('status', 'unknown')),
            'reason': last_result.get('reason'),
            'max_cycles': cycle_limit,
            'executed_cycles': executed_cycles,
            'state': self._state_dict(),
        }

    def status(self) -> Dict[str, Any]:
        return self._state_dict()

    def history(self, limit: int = 50) -> list[Dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), self._history_limit))
        return list(self._history[-normalized_limit:])

    def halt_reasons(self) -> Dict[str, Any]:
        return {
            'total_halts': int(sum(self._halt_reasons.values())),
            'counts': dict(self._halt_reasons),
            'last_halt_reason': self.state.halt_reason,
            'last_halted_at': self.state.halted_at.isoformat() if self.state.halted_at else None,
        }

    def _append_history(self, result: Dict[str, Any]) -> None:
        entry = {
            'timestamp': self._now().isoformat(),
            'status': str(result.get('status', 'unknown')),
            'reason': result.get('reason'),
            'state': dict(result.get('state') or {}),
        }
        self._history.append(entry)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]

    def _state_dict(self) -> Dict[str, Any]:
        return {
            'enabled': bool(self.state.enabled),
            'interval_seconds': int(self.state.interval_seconds),
            'last_run_at': self.state.last_run_at.isoformat() if self.state.last_run_at else None,
            'cycles_completed': int(self.state.cycles_completed),
            'halt_reason': self.state.halt_reason,
            'halted_at': self.state.halted_at.isoformat() if self.state.halted_at else None,
        }
