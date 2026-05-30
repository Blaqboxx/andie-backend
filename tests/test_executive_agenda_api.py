from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

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


if __name__ == '__main__':
    unittest.main()
