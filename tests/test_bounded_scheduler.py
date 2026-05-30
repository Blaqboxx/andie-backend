import tempfile
import unittest
from pathlib import Path

from executive.bounded_scheduler import BoundedScheduler
from executive.controller import ExecutiveController
from executive.models import ExecutiveConfig


class BoundedSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)

        self.controller = ExecutiveController(
            config=ExecutiveConfig(
                store_path=str(self.root / 'executive_state.json'),
                simulate_execution=True,
            )
        )

    def test_agenda_path_enforcement_runs_executive_cycle_only(self) -> None:
        scheduler = BoundedScheduler(self.controller, interval_seconds=15)
        scheduler.start()

        before_counts = {
            'intents': len(self.controller.store.list_intents()),
            'proposals': len(self.controller.store.list_proposals()),
            'mutations': len(self.controller.store.list_world_mutations()),
        }

        result = scheduler.run_once()

        self.assertEqual(result['status'], 'ran')
        self.assertEqual(result['state']['cycles_completed'], 1)
        self.assertIsNotNone(self.controller.store.get_executive_agenda())

        after_counts = {
            'intents': len(self.controller.store.list_intents()),
            'proposals': len(self.controller.store.list_proposals()),
            'mutations': len(self.controller.store.list_world_mutations()),
        }
        self.assertEqual(after_counts, before_counts)

        self.assertFalse(hasattr(scheduler, 'create_intent'))
        self.assertFalse(hasattr(scheduler, 'submit_proposal'))
        self.assertFalse(hasattr(scheduler, 'execute_proposal'))
        self.assertFalse(hasattr(scheduler, 'mutate_world'))

    def test_governance_preservation_no_direct_world_mutation_path(self) -> None:
        self.controller.run_agenda_loop(
            [
                {
                    'signal_id': 'sentinel:alert:scheduler-governance',
                    'institution_id': 'sentinel',
                    'type': 'security_alert',
                }
            ],
            defer_threshold=45,
        )
        self.assertGreaterEqual(len(self.controller.store.list_intents()), 1)

        scheduler = BoundedScheduler(self.controller, interval_seconds=15)
        scheduler.start()
        result = scheduler.run_once()

        self.assertEqual(result['status'], 'ran')
        self.assertEqual(len(self.controller.store.list_world_mutations()), 0)

        with self.assertRaises(PermissionError):
            self.controller.apply_world_mutation(
                actor='scheduler',
                institution='mission_control',
                proposal_id='scheduler-bypass',
                mutation_type='resource.update_quantity',
                target_entity='gpu_time',
                payload={'quantity': 1.0},
            )

    def test_policy_violation_halts_scheduler(self) -> None:
        with self.assertRaises(PermissionError):
            self.controller.submit_proposal(
                institution_id='workshop',
                proposal_type='policy_update',
                payload={},
            )

        scheduler = BoundedScheduler(self.controller, interval_seconds=15)
        scheduler.start()
        result = scheduler.run_once()

        self.assertEqual(result['status'], 'halted')
        self.assertEqual(result['reason'], 'policy_violation_rate')
        self.assertEqual(result['state']['cycles_completed'], 0)
        self.assertEqual(result['state']['halt_reason'], 'policy_violation_rate')

    def test_budget_breach_halts_scheduler(self) -> None:
        budget_controller = ExecutiveController(
            config=ExecutiveConfig(
                store_path=str(self.root / 'budget_state.json'),
                simulate_execution=True,
                max_dispatches=1,
                max_resource_cost=0.1,
                max_cycles_per_run=1,
            )
        )

        mission = budget_controller.create_mission('Budget breach mission')
        budget_controller.create_goal('Budget breach goal', mission_id=mission.mission_id)
        budget_controller.run_cycle()
        self.assertTrue(budget_controller.budget_breach())

        scheduler = BoundedScheduler(budget_controller, interval_seconds=15)
        scheduler.start()
        result = scheduler.run_once()

        self.assertEqual(result['status'], 'halted')
        self.assertEqual(result['reason'], 'budget_breach')
        self.assertEqual(result['state']['cycles_completed'], 0)

    def test_stale_intent_threshold_halts_scheduler(self) -> None:
        self.controller.run_agenda_loop(
            [
                {
                    'signal_id': 'workshop:proposal:scheduler-stale',
                    'institution_id': 'workshop',
                    'type': 'tool_proposal',
                }
            ],
            defer_threshold=45,
        )

        for _ in range(11):
            self.controller.run_agenda_loop([], defer_threshold=45)

        self.assertTrue(self.controller.stale_intent_threshold_exceeded())

        scheduler = BoundedScheduler(self.controller, interval_seconds=15)
        scheduler.start()
        result = scheduler.run_once()

        self.assertEqual(result['status'], 'halted')
        self.assertEqual(result['reason'], 'stale_intent_threshold')
        self.assertEqual(result['state']['cycles_completed'], 0)


if __name__ == '__main__':
    unittest.main()
