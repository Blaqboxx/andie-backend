import tempfile
import unittest
from pathlib import Path

from executive.a2a import LocalA2ARouter
from executive.controller import ExecutiveController
from executive.models import ExecutiveConfig


class LocalA2ARouterConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        store_path = Path(self.tmpdir.name) / 'executive_state.json'
        self.controller = ExecutiveController(
            config=ExecutiveConfig(store_path=str(store_path), simulate_execution=True)
        )
        self.router = LocalA2ARouter(self.controller)

    def test_requires_correlation_id(self) -> None:
        with self.assertRaises(ValueError):
            self.router.send_message(
                sender='academy',
                receiver='workshop',
                message_type='research_request',
                session_id='session_conformance_1',
                correlation_id='',
                payload={'topic': 'materials'},
            )

        session_items = self.router.list_session_messages('session_conformance_1')
        self.assertEqual(len(session_items), 1)
        self.assertEqual(session_items[0]['status'], 'rejected')
        self.assertEqual(session_items[0]['error_code'], 'correlation_id_required')

    def test_unknown_identity_is_rejected_and_audited(self) -> None:
        with self.assertRaises(ValueError):
            self.router.send_message(
                sender='unknown_institution',
                receiver='workshop',
                message_type='research_request',
                session_id='session_conformance_2',
                correlation_id='corr_conformance_2',
                payload={'topic': 'routing'},
            )

        session_items = self.router.list_session_messages('session_conformance_2')
        self.assertEqual(len(session_items), 1)
        self.assertEqual(session_items[0]['status'], 'rejected')
        self.assertEqual(session_items[0]['error_code'], 'identity_failure')

    def test_governance_rejection_is_audited(self) -> None:
        with self.assertRaises(PermissionError):
            self.router.send_message(
                sender='academy',
                receiver='workshop',
                message_type='world_mutation',
                session_id='session_conformance_3',
                correlation_id='corr_conformance_3',
                payload={'target': 'gpu_time'},
            )

        session_items = self.router.list_session_messages('session_conformance_3')
        self.assertEqual(len(session_items), 1)
        self.assertEqual(session_items[0]['status'], 'rejected')
        self.assertEqual(session_items[0]['error_code'], 'governance_rejection')

    def test_timeout_transitions_to_timed_out(self) -> None:
        message = self.router.send_message(
            sender='academy',
            receiver='workshop',
            message_type='research_request',
            session_id='session_conformance_4',
            correlation_id='corr_conformance_4',
            payload={'topic': 'latency'},
            timeout_seconds=1,
        )

        persisted = self.controller.store.get_a2a_message(message['message_id'])
        self.assertIsNotNone(persisted)
        persisted.timeout_at = '1970-01-01T00:00:00+00:00'
        self.controller.store.append_a2a_message(persisted)

        with self.assertRaises(ValueError) as ctx:
            self.router.respond_message(message['message_id'], {'status': 'accepted'})
        self.assertEqual(str(ctx.exception), 'a2a_message_timed_out')

        updated = self.router.get_message(message['message_id'])
        self.assertEqual(updated['status'], 'timed_out')
        self.assertEqual(updated['error_code'], 'timeout')

    def test_terminal_state_rejects_additional_transition(self) -> None:
        message = self.router.send_message(
            sender='academy',
            receiver='workshop',
            message_type='research_request',
            session_id='session_conformance_5',
            correlation_id='corr_conformance_5',
            payload={'topic': 'energy'},
        )
        self.router.respond_message(message['message_id'], {'status': 'accepted'})

        with self.assertRaises(ValueError) as ctx:
            self.router.respond_message(message['message_id'], {'status': 'accepted_again'})
        self.assertEqual(str(ctx.exception), 'a2a_message_terminal_state')

    def test_workflow_preserves_correlation_chain(self) -> None:
        workflow = self.router.run_research_prototype_workflow(
            session_id='session_conformance_6',
            topic='battery optimization',
        )
        self.assertTrue(workflow['completed'])

        items = self.router.list_session_messages('session_conformance_6', limit=10)
        self.assertGreaterEqual(len(items), 2)
        self.assertEqual(items[0]['correlation_id'], items[1]['correlation_id'])


if __name__ == '__main__':
    unittest.main()
