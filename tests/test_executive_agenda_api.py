from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from executive.bounded_scheduler import BoundedScheduler
from executive.controller import ExecutiveController
from executive.models import ExecutiveConfig
from interfaces.api.main import app


class ExecutiveAgendaApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        store_path = Path(self.tmpdir.name) / 'executive_state.json'
        self.controller = ExecutiveController(
            config=ExecutiveConfig(store_path=str(store_path), simulate_execution=True)
        )
        app.state.executive_controller = self.controller
        self.addCleanup(self._cleanup_app_state)

        self.client = TestClient(app)

    def _cleanup_app_state(self) -> None:
        if hasattr(app.state, 'executive_controller'):
            delattr(app.state, 'executive_controller')
        if hasattr(app.state, 'bounded_scheduler'):
            delattr(app.state, 'bounded_scheduler')

    def test_get_agenda_returns_current_snapshot(self) -> None:
        self.controller.run_agenda_loop(
            [
                {'signal_id': 'sentinel:alert:summary', 'institution_id': 'sentinel', 'type': 'security_alert'},
                {'signal_id': 'academy:research:summary', 'institution_id': 'academy', 'type': 'research_result'},
            ],
            defer_threshold=45,
        )
        response = self.client.get('/executive/agenda')
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertIn('agenda', payload)
        self.assertEqual(payload['agenda'].get('schema_version'), 'g1-alpha/v1')
        self.assertIn('summary', payload)
        self.assertEqual(payload['summary']['active_priority'], 'sentinel:alert:summary')
        self.assertGreaterEqual(payload['summary']['blocked_count'], 1)
        self.assertIn(payload['summary']['budget_status'], {'healthy', 'elevated', 'constrained'})

    def test_get_agenda_decisions_respects_limit_and_returns_latest_first(self) -> None:
        self.controller.run_agenda_loop(
            [{'signal_id': 'workshop:proposal:1', 'institution_id': 'workshop', 'type': 'tool_proposal'}]
        )
        self.controller.run_agenda_loop(
            [{'signal_id': 'academy:research:2', 'institution_id': 'academy', 'type': 'research_result'}]
        )
        self.controller.run_agenda_loop(
            [{'signal_id': 'sentinel:alert:3', 'institution_id': 'sentinel', 'type': 'security_alert'}]
        )

        response = self.client.get('/executive/agenda/decisions?limit=2')
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['count'], 2)
        self.assertEqual(payload['items'][0]['selected_priority'], 'sentinel:alert:3')

    def test_get_agenda_decision_by_id_and_missing_returns_404(self) -> None:
        result = self.controller.run_agenda_loop(
            [{'signal_id': 'sentinel:alert:9', 'institution_id': 'sentinel', 'type': 'security_alert'}]
        )
        decision_id = result['decision']['decision_id']

        found = self.client.get(f'/executive/agenda/decisions/{decision_id}')
        self.assertEqual(found.status_code, 200)
        found_payload = found.json()
        self.assertEqual(found_payload['status'], 'ok')
        self.assertEqual(found_payload['decision']['decision_id'], decision_id)

        missing = self.client.get('/executive/agenda/decisions/decision_missing')
        self.assertEqual(missing.status_code, 404)

    def test_get_agenda_explain_returns_active_priority_rationale_and_policy(self) -> None:
        self.controller.run_agenda_loop(
            [{'signal_id': 'sentinel:alert:explain', 'institution_id': 'sentinel', 'type': 'security_alert'}]
        )

        response = self.client.get('/executive/agenda/explain')
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['status'], 'ok')
        self.assertIsNotNone(payload['active_priority'])
        self.assertEqual(payload['active_priority']['priority_id'], 'sentinel:alert:explain')
        self.assertGreaterEqual(len(payload['rationale']), 10)
        self.assertIn('max_deferred_cycles', payload['policy'])

    def test_get_agenda_replay_reconstructs_cycle_history(self) -> None:
        self.controller.run_agenda_loop(
            [{'signal_id': 'workshop:proposal:r1', 'institution_id': 'workshop', 'type': 'tool_proposal'}]
        )
        self.controller.run_agenda_loop(
            [{'signal_id': 'academy:research:r2', 'institution_id': 'academy', 'type': 'research_result'}]
        )
        self.controller.run_agenda_loop(
            [{'signal_id': 'sentinel:alert:r3', 'institution_id': 'sentinel', 'type': 'security_alert'}]
        )

        response = self.client.get('/executive/agenda/replay?cycle=2')
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['cycle'], 2)
        self.assertEqual(payload['latest_selected_priority'], 'academy:research:r2')
        self.assertEqual(payload['selected_counts']['workshop:proposal:r1'], 1)

        out_of_range = self.client.get('/executive/agenda/replay?cycle=99')
        self.assertEqual(out_of_range.status_code, 404)

    def test_post_agenda_simulate_projects_without_state_mutation(self) -> None:
        self.controller.run_agenda_loop(
            [{'signal_id': 'workshop:proposal:sim', 'institution_id': 'workshop', 'type': 'tool_proposal'}],
            defer_threshold=60,
        )
        before_decisions = len(self.controller.store.list_agenda_decisions())
        before_agenda = self.controller.store.get_executive_agenda()
        self.assertIsNotNone(before_agenda)
        before_state = dict(before_agenda.agenda_item_state)

        response = self.client.post(
            '/executive/agenda/simulate',
            json={
                'signals': [
                    {'signal_id': 'workshop:proposal:sim', 'institution_id': 'workshop', 'type': 'tool_proposal'},
                    {'signal_id': 'sentinel:alert:sim', 'institution_id': 'sentinel', 'type': 'security_alert'},
                ],
                'policy': {'max_deferred_cycles': 1, 'sentinel_escalation_rate': 1.5},
                'defer_threshold': 60,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')

        simulation = payload['simulation']
        self.assertFalse(simulation['state_mutated'])
        self.assertEqual(simulation['predicted_priority_order'][0], 'sentinel:alert:sim')
        self.assertIn('expected_escalations', simulation)
        self.assertIn('budget_effects', simulation)

        after_decisions = len(self.controller.store.list_agenda_decisions())
        self.assertEqual(after_decisions, before_decisions)
        after_agenda = self.controller.store.get_executive_agenda()
        self.assertIsNotNone(after_agenda)
        self.assertEqual(dict(after_agenda.agenda_item_state), before_state)

    def test_intent_lifecycle_endpoints_track_creation_and_updates(self) -> None:
        run = self.controller.run_agenda_loop(
            [
                {'signal_id': 'sentinel:alert:intent', 'institution_id': 'sentinel', 'type': 'security_alert'},
                {'signal_id': 'workshop:proposal:intent', 'institution_id': 'workshop', 'type': 'tool_proposal'},
            ],
            defer_threshold=45,
        )
        self.assertGreaterEqual(len(run['intents']), 2)
        intent_id = run['intents'][0]['intent_id']

        listed = self.client.get('/executive/intents?status=created&limit=10')
        self.assertEqual(listed.status_code, 200)
        listed_payload = listed.json()
        self.assertEqual(listed_payload['status'], 'ok')
        self.assertGreaterEqual(listed_payload['count'], 1)

        found = self.client.get(f'/executive/intents/{intent_id}')
        self.assertEqual(found.status_code, 200)
        found_payload = found.json()
        self.assertEqual(found_payload['intent']['intent_id'], intent_id)
        self.assertEqual(found_payload['intent']['status'], 'created')

        updated = self.client.post(
            f'/executive/intents/{intent_id}/status',
            json={'status': 'in_progress', 'completion_state': 'running'},
        )
        self.assertEqual(updated.status_code, 200)
        updated_payload = updated.json()
        self.assertEqual(updated_payload['intent']['status'], 'in_progress')
        self.assertEqual(updated_payload['intent']['completion_state'], 'running')

        completed = self.client.post(
            f'/executive/intents/{intent_id}/status',
            json={'status': 'completed', 'completion_state': 'done'},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()['intent']['status'], 'completed')

        missing = self.client.get('/executive/intents/intent_missing')
        self.assertEqual(missing.status_code, 404)

    def test_get_executive_slo_returns_operational_readiness_snapshot(self) -> None:
        self.controller.run_agenda_loop(
            [
                {'signal_id': 'sentinel:alert:slo-api', 'institution_id': 'sentinel', 'type': 'security_alert'},
                {'signal_id': 'academy:research:slo-api', 'institution_id': 'academy', 'type': 'research_result'},
            ],
            defer_threshold=45,
        )
        self.controller.simulate_agenda_loop(
            [
                {'signal_id': 'workshop:proposal:slo-api', 'institution_id': 'workshop', 'type': 'tool_proposal'},
            ],
            defer_threshold=45,
        )

        response = self.client.get('/executive/slo')
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['status'], 'ok')
        self.assertIn('targets', payload)
        self.assertIn('metrics', payload)
        self.assertIn('summary', payload)
        self.assertIn('executive', payload['metrics'])
        self.assertIn('intent', payload['metrics'])
        self.assertIn('governance', payload['metrics'])
        self.assertGreaterEqual(payload['metrics']['executive']['decision_latency']['p95_ms'], 0)
        self.assertEqual(payload['metrics']['governance']['simulation_state_mutations']['value'], 0)

    def test_scheduler_status_history_and_halt_reasons_endpoints(self) -> None:
        scheduler = BoundedScheduler(self.controller, interval_seconds=7)
        app.state.bounded_scheduler = scheduler

        status_before = self.client.get('/scheduler/status')
        self.assertEqual(status_before.status_code, 200)
        self.assertEqual(status_before.json()['scheduler']['enabled'], False)
        self.assertEqual(status_before.json()['scheduler']['cycles_completed'], 0)

        scheduler.start()
        first_run = scheduler.run_once()
        self.assertEqual(first_run['status'], 'ran')

        history = self.client.get('/scheduler/history?limit=10')
        self.assertEqual(history.status_code, 200)
        history_payload = history.json()
        self.assertEqual(history_payload['status'], 'ok')
        self.assertGreaterEqual(history_payload['count'], 1)
        self.assertEqual(history_payload['items'][-1]['status'], 'ran')

        # Force a policy violation so next cycle halts.
        with self.assertRaises(PermissionError):
            self.controller.submit_proposal(
                institution_id='workshop',
                proposal_type='policy_update',
                payload={},
            )
        halted = scheduler.run_once()
        self.assertEqual(halted['status'], 'halted')
        self.assertEqual(halted['reason'], 'policy_violation_rate')

        reasons = self.client.get('/scheduler/halt-reasons')
        self.assertEqual(reasons.status_code, 200)
        reasons_payload = reasons.json()
        self.assertEqual(reasons_payload['status'], 'ok')
        self.assertEqual(reasons_payload['halt_reasons']['counts']['policy_violation_rate'], 1)
        self.assertEqual(reasons_payload['halt_reasons']['last_halt_reason'], 'policy_violation_rate')

    def test_intent_outcome_feedback_updates_agenda_and_history(self) -> None:
        run = self.controller.run_agenda_loop(
            [
                {'signal_id': 'workshop:proposal:feedback', 'institution_id': 'workshop', 'type': 'tool_proposal'},
                {'signal_id': 'sentinel:alert:feedback', 'institution_id': 'sentinel', 'type': 'security_alert'},
            ],
            defer_threshold=45,
        )
        self.assertGreaterEqual(len(run['intents']), 1)
        intent_id = run['intents'][0]['intent_id']
        source_priority = run['intents'][0]['signal_id']

        updated = self.client.post(
            f'/executive/intents/{intent_id}/status',
            json={'status': 'failed', 'completion_state': 'stalled'},
        )
        self.assertEqual(updated.status_code, 200)

        agenda = self.client.get('/executive/agenda')
        self.assertEqual(agenda.status_code, 200)
        agenda_state = agenda.json()['agenda']['agenda_item_state']
        self.assertIn(source_priority, agenda_state)
        self.assertEqual(agenda_state[source_priority]['last_intent_status'], 'failed')
        self.assertEqual(agenda_state[source_priority]['last_completion_state'], 'stalled')
        self.assertTrue(agenda_state[source_priority]['needs_replan'])

        outcomes = self.client.get('/executive/intent-outcomes?limit=10')
        self.assertEqual(outcomes.status_code, 200)
        payload = outcomes.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertGreaterEqual(payload['count'], 1)
        self.assertEqual(payload['items'][0]['intent_id'], intent_id)
        self.assertEqual(payload['items'][0]['status'], 'failed')

    def test_scheduler_control_endpoints_run_once_run_cycles_and_until_halt(self) -> None:
        scheduler = BoundedScheduler(self.controller, interval_seconds=3)
        app.state.bounded_scheduler = scheduler

        run_once = self.client.post('/scheduler/run-once', json={})
        self.assertEqual(run_once.status_code, 200)
        run_once_payload = run_once.json()
        self.assertEqual(run_once_payload['status'], 'ok')
        self.assertEqual(run_once_payload['result']['status'], 'ran')

        run_cycles = self.client.post('/scheduler/run-cycles', json={'cycles': 2})
        self.assertEqual(run_cycles.status_code, 200)
        run_cycles_payload = run_cycles.json()
        self.assertEqual(run_cycles_payload['status'], 'ok')
        self.assertEqual(run_cycles_payload['result']['requested_cycles'], 2)
        self.assertEqual(run_cycles_payload['result']['executed_cycles'], 2)

        with self.assertRaises(PermissionError):
            self.controller.submit_proposal(
                institution_id='workshop',
                proposal_type='policy_update',
                payload={},
            )

        until_halt = self.client.post('/scheduler/run-until-halt', json={'max_cycles': 5})
        self.assertEqual(until_halt.status_code, 200)
        until_halt_payload = until_halt.json()
        self.assertEqual(until_halt_payload['status'], 'ok')
        self.assertEqual(until_halt_payload['result']['status'], 'halted')
        self.assertEqual(until_halt_payload['result']['reason'], 'policy_violation_rate')
        self.assertEqual(until_halt_payload['result']['executed_cycles'], 0)


if __name__ == '__main__':
    unittest.main()
