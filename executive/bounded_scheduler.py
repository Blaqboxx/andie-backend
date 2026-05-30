from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

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

    def _run_once_core(self) -> Dict[str, Any]:
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

    def run_once(self) -> Dict[str, Any]:
        session = self._start_session(mode='run_once')
        result = self._run_once_core()
        self._append_session_event(session, result)

        status = str(result.get('status', 'unknown'))
        if status == 'ran':
            self._finalize_session(session, state='completed', stop_reason=None)
        elif status == 'halted':
            self._finalize_session(session, state='halted', stop_reason=str(result.get('reason') or 'halted'))
        else:
            self._finalize_session(session, state='aborted', stop_reason=str(result.get('reason') or status))

        return {
            **result,
            'session_id': session['session_id'],
        }

    def run_cycles(self, cycles: int) -> Dict[str, Any]:
        requested_cycles = max(1, int(cycles))
        executed_cycles = 0
        last_result: Dict[str, Any] | None = None
        session = self._start_session(mode='run_cycles')

        for _ in range(requested_cycles):
            last_result = self._run_once_core()
            self._append_session_event(session, last_result)
            if str(last_result.get('status')) != 'ran':
                break
            executed_cycles += 1

        if last_result is None:
            last_result = {
                'status': 'skipped',
                'reason': 'scheduler_disabled',
                'state': self._state_dict(),
            }

        final_status = str(last_result.get('status', 'unknown'))
        if final_status == 'halted':
            self._finalize_session(session, state='halted', stop_reason=str(last_result.get('reason') or 'halted'))
        elif final_status == 'skipped':
            self._finalize_session(session, state='aborted', stop_reason=str(last_result.get('reason') or 'skipped'))
        else:
            self._finalize_session(session, state='completed', stop_reason=None)

        return {
            'status': str(last_result.get('status', 'unknown')),
            'reason': last_result.get('reason'),
            'requested_cycles': requested_cycles,
            'executed_cycles': executed_cycles,
            'state': self._state_dict(),
            'session_id': session['session_id'],
        }

    def run_until_halt(self, max_cycles: int = 100) -> Dict[str, Any]:
        cycle_limit = max(1, int(max_cycles))
        executed_cycles = 0
        last_result: Dict[str, Any] | None = None
        session = self._start_session(mode='run_until_halt')

        while executed_cycles < cycle_limit:
            last_result = self._run_once_core()
            self._append_session_event(session, last_result)
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
            self._finalize_session(session, state='completed', stop_reason='max_cycles_reached')
            return {
                'status': 'max_cycles_reached',
                'reason': None,
                'max_cycles': cycle_limit,
                'executed_cycles': executed_cycles,
                'state': self._state_dict(),
                'session_id': session['session_id'],
            }

        final_status = str(last_result.get('status', 'unknown'))
        if final_status == 'halted':
            self._finalize_session(session, state='halted', stop_reason=str(last_result.get('reason') or 'halted'))
        elif final_status == 'skipped':
            self._finalize_session(session, state='aborted', stop_reason=str(last_result.get('reason') or 'skipped'))
        else:
            self._finalize_session(session, state='completed', stop_reason=None)

        return {
            'status': str(last_result.get('status', 'unknown')),
            'reason': last_result.get('reason'),
            'max_cycles': cycle_limit,
            'executed_cycles': executed_cycles,
            'state': self._state_dict(),
            'session_id': session['session_id'],
        }

    def list_sessions(self, limit: int = 50) -> list[Dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        sessions = self.controller.store.list_autonomy_sessions()
        return list(reversed(sessions[-normalized_limit:]))

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.controller.store.get_autonomy_session(session_id)

    def replay_session(self, session_id: str) -> Dict[str, Any]:
        session = self.controller.store.get_autonomy_session(session_id)
        if not isinstance(session, dict):
            return {'found': False, 'items': []}
        events = list(session.get('events') or [])
        return {
            'found': True,
            'session_id': session_id,
            'count': len(events),
            'items': events,
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

    def _collect_runtime_counters(self) -> Dict[str, Any]:
        intents = self.controller.store.list_intents()
        proposals = self.controller.store.list_proposals()
        audits = self.controller.store.list_cycle_audits()
        op_metrics = self.controller.store.get_operational_metrics()
        agenda = self.controller.store.get_executive_agenda()

        stalled_states = {'stalled', 'blocked'}
        return {
            'intents_total': len(intents),
            'intents_completed': len([item for item in intents if item.status.value == 'completed']),
            'intents_failed': len([item for item in intents if item.status.value in {'failed', 'cancelled'}]),
            'intents_stalled': len([item for item in intents if str(item.completion_state) in stalled_states]),
            'proposals_total': len(proposals),
            'proposals_approved': len([
                item
                for item in proposals
                if item.status.value in {'approved', 'executed'}
            ]),
            'policy_violations': int(op_metrics.get('policy_violations', 0)),
            'budget_consumed': round(sum(float(item.resources_consumed) for item in audits), 3),
            'active_priority': (
                str((agenda.priorities[0] or {}).get('priority_id', ''))
                if agenda and list(agenda.priorities or [])
                else None
            ),
        }

    def _start_session(self, *, mode: str) -> Dict[str, Any]:
        now = self._now().isoformat()
        baseline = self._collect_runtime_counters()
        session = {
            'session_id': f'session_{uuid4().hex}',
            'mode': str(mode),
            'started_at': now,
            'ended_at': None,
            'state': 'running',
            'stop_reason': None,
            'cycles_executed': 0,
            'intents_created': 0,
            'intents_completed': 0,
            'intents_failed': 0,
            'intents_stalled': 0,
            'proposals_created': 0,
            'proposals_approved': 0,
            'policy_violations': 0,
            'budget_consumed': 0.0,
            'active_priority': baseline.get('active_priority'),
            'summary': 'session running',
            'events': [],
            'baseline': baseline,
        }
        return self.controller.store.upsert_autonomy_session(session)

    def _append_session_event(self, session: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        refreshed = self.controller.store.get_autonomy_session(str(session.get('session_id'))) or dict(session)
        events = list(refreshed.get('events') or [])
        counters = self._collect_runtime_counters()
        event = {
            'timestamp': self._now().isoformat(),
            'status': str(result.get('status', 'unknown')),
            'reason': result.get('reason'),
            'active_priority': counters.get('active_priority'),
            'state': dict(result.get('state') or {}),
        }
        events.append(event)
        refreshed['events'] = events[-500:]
        if str(result.get('status')) == 'ran':
            refreshed['cycles_executed'] = int(refreshed.get('cycles_executed', 0)) + 1
        refreshed['active_priority'] = counters.get('active_priority')
        return self.controller.store.upsert_autonomy_session(refreshed)

    def _finalize_session(self, session: Dict[str, Any], *, state: str, stop_reason: Optional[str]) -> Dict[str, Any]:
        refreshed = self.controller.store.get_autonomy_session(str(session.get('session_id'))) or dict(session)
        baseline = dict(refreshed.get('baseline') or {})
        current = self._collect_runtime_counters()

        refreshed['ended_at'] = self._now().isoformat()
        refreshed['state'] = str(state)
        refreshed['stop_reason'] = stop_reason
        refreshed['intents_created'] = max(0, int(current.get('intents_total', 0)) - int(baseline.get('intents_total', 0)))
        refreshed['intents_completed'] = max(
            0,
            int(current.get('intents_completed', 0)) - int(baseline.get('intents_completed', 0)),
        )
        refreshed['intents_failed'] = max(
            0,
            int(current.get('intents_failed', 0)) - int(baseline.get('intents_failed', 0)),
        )
        refreshed['intents_stalled'] = max(
            0,
            int(current.get('intents_stalled', 0)) - int(baseline.get('intents_stalled', 0)),
        )
        refreshed['proposals_created'] = max(
            0,
            int(current.get('proposals_total', 0)) - int(baseline.get('proposals_total', 0)),
        )
        refreshed['proposals_approved'] = max(
            0,
            int(current.get('proposals_approved', 0)) - int(baseline.get('proposals_approved', 0)),
        )
        refreshed['policy_violations'] = max(
            0,
            int(current.get('policy_violations', 0)) - int(baseline.get('policy_violations', 0)),
        )
        refreshed['budget_consumed'] = round(
            max(0.0, float(current.get('budget_consumed', 0.0)) - float(baseline.get('budget_consumed', 0.0))),
            3,
        )
        refreshed['active_priority'] = current.get('active_priority')
        refreshed['summary'] = (
            f"Session {refreshed.get('session_id')} {state}; cycles={refreshed.get('cycles_executed', 0)}; "
            f"intents created={refreshed.get('intents_created', 0)} completed={refreshed.get('intents_completed', 0)} "
            f"failed={refreshed.get('intents_failed', 0)}; policy_violations={refreshed.get('policy_violations', 0)}"
        )
        return self.controller.store.upsert_autonomy_session(refreshed)
