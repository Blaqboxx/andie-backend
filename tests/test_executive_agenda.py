from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from executive.controller import ExecutiveController
from executive.models import ExecutiveConfig


class ExecutiveAgendaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        store_path = Path(self.tmpdir.name) / 'state.json'
        self.controller = ExecutiveController(
            config=ExecutiveConfig(store_path=str(store_path), simulate_execution=True)
        )

    def test_agenda_updates_with_goal_flow(self) -> None:
        mission = self.controller.create_mission('Agenda mission')
        goal = self.controller.create_goal('Agenda goal', mission_id=mission.mission_id, priority='high')

        self.controller._refresh_executive_agenda()
        agenda = self.controller.store.get_executive_agenda()

        self.assertIsNotNone(agenda)
        self.assertIn(goal.goal_id, agenda.strategic_priorities)

        self.controller.run_cycle()
        agenda_after_cycle = self.controller.store.get_executive_agenda()
        self.assertIsNotNone(agenda_after_cycle)

    def test_agenda_tracks_pending_and_resolved_proposals(self) -> None:
        proposal = self.controller.submit_proposal(
            institution_id='workshop',
            proposal_type='world_mutation',
            payload={
                'mutation_type': 'resource.update_quantity',
                'target_entity': 'gpu_time',
                'payload': {'quantity': 1100.0},
            },
        )

        agenda_pending = self.controller.store.get_executive_agenda()
        self.assertIsNotNone(agenda_pending)
        self.assertIn(proposal['proposal_id'], agenda_pending.pending_proposals)
        self.assertIn('workshop', agenda_pending.institution_requests)

        self.controller.review_proposal(proposal['proposal_id'], approve=True, rationale='approved')
        self.controller.execute_proposal(proposal['proposal_id'])

        agenda_resolved = self.controller.store.get_executive_agenda()
        self.assertIsNotNone(agenda_resolved)
        self.assertNotIn(proposal['proposal_id'], agenda_resolved.pending_proposals)


if __name__ == '__main__':
    unittest.main()
