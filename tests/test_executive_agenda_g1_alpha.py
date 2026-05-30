from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from executive.controller import ExecutiveController
from executive.models import ExecutiveConfig


class ExecutiveAgendaG1AlphaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        root = Path(self.tmpdir.name)
        self.store_path = root / 'executive_state.json'
        self.controller = ExecutiveController(
            config=ExecutiveConfig(store_path=str(self.store_path), simulate_execution=True)
        )

    def test_agenda_loop_ranks_competing_signals_and_restores_after_restart(self) -> None:
        signals = [
            {'signal_id': 'workshop:proposal:1', 'institution_id': 'workshop', 'type': 'tool_proposal'},
            {'signal_id': 'academy:research:1', 'institution_id': 'academy', 'type': 'research_result'},
            {'signal_id': 'sentinel:alert:1', 'institution_id': 'sentinel', 'type': 'security_alert'},
        ]

        result = self.controller.run_agenda_loop(signals, defer_threshold=45)

        ordered = [item['signal_id'] for item in result['ranked_priorities']]
        self.assertEqual(
            ordered,
            ['sentinel:alert:1', 'workshop:proposal:1', 'academy:research:1'],
        )

        statuses = {item['signal_id']: item['status'] for item in result['ranked_priorities']}
        self.assertEqual(statuses['sentinel:alert:1'], 'ready')
        self.assertEqual(statuses['workshop:proposal:1'], 'ready')
        self.assertEqual(statuses['academy:research:1'], 'deferred')

        intents = [item['intent_type'] for item in result['intents']]
        self.assertEqual(intents, ['review_sentinel_alert', 'evaluate_workshop_proposal'])

        decision = result['decision']
        self.assertEqual(decision['selected_priority'], 'sentinel:alert:1')
        self.assertIn('academy:research:1', decision['rejected_priorities'])
        self.assertGreaterEqual(len(decision['rationale']), 20)

        agenda_file = self.store_path.parent / 'agenda.json'
        ledger_file = self.store_path.parent / 'agenda_decisions.jsonl'
        self.assertTrue(agenda_file.exists())
        self.assertTrue(ledger_file.exists())

        restarted = ExecutiveController(
            config=ExecutiveConfig(store_path=str(self.store_path), simulate_execution=True)
        )
        restored_agenda = restarted.store.get_executive_agenda()
        self.assertIsNotNone(restored_agenda)
        self.assertEqual(
            restored_agenda.strategic_priorities[:3],
            ['sentinel:alert:1', 'workshop:proposal:1', 'academy:research:1'],
        )

        restored_decisions = restarted.store.list_agenda_decisions()
        self.assertGreaterEqual(len(restored_decisions), 1)
        self.assertEqual(restored_decisions[-1].selected_priority, 'sentinel:alert:1')
        self.assertIn('evaluate_workshop_proposal', restored_decisions[-1].emitted_intents)

    def test_multi_cycle_aging_escalates_repeated_deferred_item(self) -> None:
        workshop_signal = [
            {'signal_id': 'workshop:proposal:aging', 'institution_id': 'workshop', 'type': 'tool_proposal'}
        ]

        first = self.controller.run_agenda_loop(workshop_signal, defer_threshold=60)
        first_item = first['ranked_priorities'][0]
        self.assertEqual(first_item['status'], 'deferred')
        self.assertEqual(first_item['score'], 50)
        self.assertEqual(first_item['age_cycles'], 1)
        self.assertEqual(first_item['deferred_count'], 1)

        second = self.controller.run_agenda_loop(workshop_signal, defer_threshold=60)
        second_item = second['ranked_priorities'][0]
        self.assertEqual(second_item['status'], 'deferred')
        self.assertEqual(second_item['age_cycles'], 2)
        self.assertEqual(second_item['deferred_count'], 2)

        third = self.controller.run_agenda_loop(workshop_signal, defer_threshold=60)
        third_item = third['ranked_priorities'][0]
        self.assertEqual(third_item['status'], 'deferred')
        self.assertEqual(third_item['age_cycles'], 3)
        self.assertEqual(third_item['deferred_count'], 3)

        fourth = self.controller.run_agenda_loop(workshop_signal, defer_threshold=60)
        fourth_item = fourth['ranked_priorities'][0]
        self.assertEqual(fourth_item['status'], 'ready')
        self.assertGreaterEqual(fourth_item['escalation_boost'], 15)
        self.assertGreaterEqual(fourth_item['score'], 65)

    def test_policy_file_controls_deferred_escalation_threshold(self) -> None:
        policy_path = Path(self.tmpdir.name) / 'agenda_policy.json'
        policy_path.write_text(
            '{"max_deferred_cycles": 1, "sentinel_escalation_rate": 1.0, "academy_decay_rate": 1.0, "blocker_escalation_threshold": 3}',
            encoding='utf-8',
        )

        with patch.dict(os.environ, {'ANDIE_AGENDA_POLICY_PATH': str(policy_path)}, clear=False):
            controller = ExecutiveController(
                config=ExecutiveConfig(store_path=str(Path(self.tmpdir.name) / 'policy_state.json'), simulate_execution=True)
            )

        workshop_signal = [
            {'signal_id': 'workshop:proposal:policy', 'institution_id': 'workshop', 'type': 'tool_proposal'}
        ]
        first = controller.run_agenda_loop(workshop_signal, defer_threshold=60)
        self.assertEqual(first['ranked_priorities'][0]['status'], 'deferred')
        second = controller.run_agenda_loop(workshop_signal, defer_threshold=60)
        self.assertEqual(second['ranked_priorities'][0]['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
