from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from executive.controller import ExecutiveController
from executive.models import ExecutiveConfig


class GovernanceInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        store_path = Path(self.tmpdir.name) / "state.json"
        self.controller = ExecutiveController(
            config=ExecutiveConfig(store_path=str(store_path), simulate_execution=True)
        )

    def test_institution_cannot_mutate_directly(self) -> None:
        with self.assertRaises(PermissionError):
            self.controller.apply_world_mutation(
                actor="institution:workshop",
                institution="workshop",
                proposal_id="bypass",
                mutation_type="resource.update_quantity",
                target_entity="gpu_time",
                payload={"quantity": 900.0},
            )

    def test_rejected_proposal_cannot_execute(self) -> None:
        proposal = self.controller.submit_proposal(
            institution_id="workshop",
            proposal_type="world_mutation",
            payload={
                "mutation_type": "resource.update_quantity",
                "target_entity": "gpu_time",
                "payload": {"quantity": 800.0},
            },
        )
        self.controller.review_proposal(proposal["proposal_id"], approve=False, rationale="rejected")
        with self.assertRaises(ValueError):
            self.controller.execute_proposal(proposal["proposal_id"])

    def test_constitution_violation_blocks_execution(self) -> None:
        proposal = self.controller.submit_proposal(
            institution_id="workshop",
            proposal_type="world_mutation",
            payload={
                "mutation_type": "resource.update_quantity",
                "target_entity": "gpu_time",
                "payload": {"quantity": 777.0},
            },
        )
        self.controller.review_proposal(proposal["proposal_id"], approve=True, rationale="approved")

        original_check_action = self.controller.identity.check_action

        def deny_world_mutation(action: str, context=None):
            if str(action).startswith("world_mutation:"):
                return False, "violates_hard_limit:test_policy"
            return original_check_action(action, context)

        self.controller.identity.check_action = deny_world_mutation

        with self.assertRaises(PermissionError):
            self.controller.execute_proposal(proposal["proposal_id"])

    def test_resource_budget_exceeded_triggers_rollback(self) -> None:
        cfg = ExecutiveConfig(
            store_path=str(Path(self.tmpdir.name) / "budget.json"),
            simulate_execution=True,
            max_cycles_per_run=1,
            max_dispatches=1,
            max_resource_cost=0.1,
        )
        controller = ExecutiveController(config=cfg)
        mission = controller.create_mission("Budget guard")
        controller.create_goal("Budget guard goal", mission_id=mission.mission_id)

        outcome = controller.run_cycle()
        self.assertGreaterEqual(outcome["observed_goals"], 1)

        audits = controller.store.list_cycle_audits()
        self.assertTrue(audits)
        self.assertTrue(audits[-1].rollback_triggered)


if __name__ == "__main__":
    unittest.main()
