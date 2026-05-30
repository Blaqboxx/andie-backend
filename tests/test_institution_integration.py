from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from executive.controller import ExecutiveController
from executive.models import ExecutiveConfig, ProposalStatus


class InstitutionIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        store_path = Path(self.tmpdir.name) / 'state.json'
        self.controller = ExecutiveController(
            config=ExecutiveConfig(store_path=str(store_path), simulate_execution=True)
        )

    def test_profiles_bootstrapped(self) -> None:
        profiles = self.controller.store.list_institution_profiles()
        profile_ids = {p.institution_id for p in profiles}
        self.assertEqual(
            profile_ids,
            {'workshop', 'academy', 'laboratory', 'mission_control', 'memory_vault', 'sentinel'},
        )

    def test_disallowed_proposal_type_is_rejected(self) -> None:
        with self.assertRaises(PermissionError):
            self.controller.submit_proposal(
                institution_id='academy',
                proposal_type='world_mutation',
                payload={
                    'mutation_type': 'resource.update_quantity',
                    'target_entity': 'gpu_time',
                    'payload': {'quantity': 1100.0},
                },
            )

    def test_resource_limit_is_enforced(self) -> None:
        with self.assertRaises(PermissionError):
            self.controller.submit_proposal(
                institution_id='laboratory',
                proposal_type='world_mutation',
                payload={
                    'mutation_type': 'resource.update_quantity',
                    'target_entity': 'gpu_time',
                    'payload': {'quantity': 1205.0},
                },
            )

    def test_sentinel_can_veto_approved_proposal(self) -> None:
        proposal = self.controller.submit_proposal(
            institution_id='workshop',
            proposal_type='world_mutation',
            payload={
                'mutation_type': 'resource.update_quantity',
                'target_entity': 'gpu_time',
                'payload': {'quantity': 1100.0},
            },
        )
        self.controller.review_proposal(proposal['proposal_id'], approve=True, rationale='approved')

        vetoed = self.controller.veto_proposal(proposal['proposal_id'], rationale='sentinel veto')
        self.assertEqual(vetoed['status'], ProposalStatus.REJECTED.value)
        self.assertTrue(vetoed['outcome']['vetoed'])

        with self.assertRaises(ValueError):
            self.controller.execute_proposal(proposal['proposal_id'])


if __name__ == '__main__':
    unittest.main()
