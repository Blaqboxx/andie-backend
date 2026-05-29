import tempfile
import unittest
from pathlib import Path

from executive.controller import ExecutiveController
from executive.models import ExecutiveConfig, GoalStatus, ProposalStatus


class ExecutiveControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        root = Path(self.tmpdir.name)
        self.store_path = root / 'executive_state.json'
        self.identity_path = root / 'identity_state.json'
        config = ExecutiveConfig(store_path=str(self.store_path), simulate_execution=True)
        self.controller = ExecutiveController(config=config)
        if hasattr(self.controller.identity, '_path'):
            self.controller.identity._path = self.identity_path
            self.controller.identity._save()

    def test_mission_goal_lifecycle_persists(self) -> None:
        mission = self.controller.create_mission(
            'Build a better memory system',
            objectives=['Ship durable state', 'Track reflections'],
        )
        goal = self.controller.create_goal(
            'Prototype executive loop',
            mission_id=mission.mission_id,
            success_criteria=['Generate plan', 'Dispatch tasks', 'Reflect'],
        )

        generated = self.controller.generate_plan(goal.goal_id)
        self.assertGreaterEqual(len(generated), 2)
        first_task = generated[0]

        envelope = self.controller.dispatch_task(first_task.task_id)
        self.assertEqual(envelope.task_id, first_task.task_id)

        reflected = self.controller.reflect(goal.goal_id)
        self.assertEqual(reflected.goal_id, goal.goal_id)

        reloaded = ExecutiveController(config=ExecutiveConfig(store_path=str(self.store_path), simulate_execution=True))
        loaded_goal = reloaded.load_goal(goal.goal_id)
        self.assertIsNotNone(loaded_goal)
        self.assertIn(loaded_goal.status, {GoalStatus.ACTIVE, GoalStatus.COMPLETED})

    def test_receive_callback_updates_task(self) -> None:
        mission = self.controller.create_mission('Callback mission')
        goal = self.controller.create_goal('Callback goal', mission_id=mission.mission_id)
        tasks = self.controller.generate_plan(goal.goal_id)
        task = tasks[0]

        self.controller.dispatch_task(task.task_id)
        stored_task = self.controller.store.get_task(task.task_id)
        self.assertEqual(stored_task.status.value, 'completed')
        self.assertIn('result', stored_task.outputs)

    def test_core_identity_is_immutable(self) -> None:
        before = self.controller.identity_snapshot()['core']
        with self.assertRaises(ValueError):
            self.controller.identity.set_core({'name': 'NOT-ANDIE'})
        after = self.controller.identity_snapshot()['core']
        self.assertEqual(before['name'], 'ANDIE')
        self.assertEqual(after['name'], 'ANDIE')

    def test_f2_proposal_pipeline_is_mandatory(self) -> None:
        with self.assertRaises(PermissionError):
            self.controller.apply_world_mutation(
                actor='institution:resource_council',
                institution='resource_council',
                proposal_id='manual-bypass',
                mutation_type='resource.update_quantity',
                target_entity='gpu_time',
                payload={'delta': 5.0},
            )

        proposal = self.controller.submit_proposal(
            institution_id='workshop',
            proposal_type='world_mutation',
            payload={
                'mutation_type': 'resource.update_quantity',
                'target_entity': 'gpu_time',
                'payload': {'quantity': 1200.0},
            },
        )
        reviewed = self.controller.review_proposal(proposal['proposal_id'], approve=True, rationale='approved by executive')
        self.assertEqual(reviewed['status'], ProposalStatus.APPROVED.value)

        mutation = self.controller.execute_proposal(proposal['proposal_id'])
        self.assertEqual(mutation['proposal_id'], proposal['proposal_id'])
        self.assertEqual(mutation['mutation_type'], 'resource.update_quantity')

        energy = next((r for r in self.controller.store.list_resources() if r.id == 'gpu_time'), None)
        self.assertIsNotNone(energy)
        self.assertEqual(energy.quantity, 1200.0)

        executed = self.controller.store.get_proposal(proposal['proposal_id'])
        self.assertEqual(executed.status, ProposalStatus.EXECUTED)

    def test_rejected_proposal_cannot_execute(self) -> None:
        proposal = self.controller.submit_proposal(
            institution_id='workshop',
            proposal_type='world_mutation',
            payload={
                'mutation_type': 'resource.update_quantity',
                'target_entity': 'gpu_time',
                'payload': {'quantity': 1300.0},
            },
        )
        reviewed = self.controller.review_proposal(proposal['proposal_id'], approve=False, rationale='insufficient evidence')
        self.assertEqual(reviewed['status'], ProposalStatus.REJECTED.value)

        with self.assertRaises(ValueError):
            self.controller.execute_proposal(proposal['proposal_id'])

    def test_g1_cycle_budget_and_audit(self) -> None:
        cfg = ExecutiveConfig(
            store_path=str(self.store_path),
            simulate_execution=True,
            max_cycles_per_run=1,
            max_dispatches=1,
            max_resource_cost=0.1,
        )
        controller = ExecutiveController(config=cfg)
        mission = controller.create_mission('Audit mission')
        controller.create_goal('Audit goal', mission_id=mission.mission_id)

        controller.submit_proposal(
            institution_id='workshop',
            proposal_type='world_mutation',
            payload={
                'mutation_type': 'resource.update_quantity',
                'target_entity': 'gpu_time',
                'payload': {'quantity': 1100.0},
            },
        )

        outcome = controller.run_cycle()
        self.assertGreaterEqual(outcome['observed_goals'], 1)
        self.assertGreaterEqual(outcome['dispatched_tasks'], 0)

        audits = controller.store.list_cycle_audits()
        self.assertGreaterEqual(len(audits), 1)
        latest = audits[-1]
        self.assertGreaterEqual(latest.proposals_generated, 1)
        self.assertTrue(latest.rollback_triggered)


if __name__ == '__main__':
    unittest.main()
