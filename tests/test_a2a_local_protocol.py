import tempfile
import unittest
from pathlib import Path

from executive.a2a import LocalA2ARouter
from executive.controller import ExecutiveController
from executive.models import ExecutiveConfig


class LocalA2AProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        store_path = Path(self.tmpdir.name) / 'executive_state.json'
        self.controller = ExecutiveController(
            config=ExecutiveConfig(store_path=str(store_path), simulate_execution=True)
        )
        self.router = LocalA2ARouter(self.controller)

    def test_send_and_respond_message_tracks_audit_fields(self) -> None:
        msg = self.router.send_message(
            sender='academy',
            receiver='workshop',
            message_type='research_request',
            session_id='session_test_1',
            payload={'topic': 'materials'},
        )
        self.assertTrue(msg['message_id'].startswith('a2a_'))
        self.assertEqual(msg['sender'], 'academy')
        self.assertEqual(msg['receiver'], 'workshop')
        self.assertEqual(msg['session_id'], 'session_test_1')
        self.assertIn('timestamp', msg)
        self.assertEqual(msg['request']['topic'], 'materials')
        self.assertEqual(msg['status'], 'delivered')

        replied = self.router.respond_message(msg['message_id'], {'status': 'accepted'})
        self.assertEqual(replied['status'], 'responded')
        self.assertEqual(replied['response']['status'], 'accepted')

    def test_session_and_inbox_queries_filter_messages(self) -> None:
        self.router.send_message(
            sender='academy',
            receiver='workshop',
            message_type='research_request',
            session_id='session_alpha',
            payload={'item': 1},
        )
        self.router.send_message(
            sender='academy',
            receiver='workshop',
            message_type='research_request',
            session_id='session_beta',
            payload={'item': 2},
        )

        session_msgs = self.router.list_session_messages('session_alpha', limit=10)
        self.assertEqual(len(session_msgs), 1)
        self.assertEqual(session_msgs[0]['session_id'], 'session_alpha')

        inbox_msgs = self.router.inbox('workshop', session_id='session_beta', limit=10)
        self.assertEqual(len(inbox_msgs), 1)
        self.assertEqual(inbox_msgs[0]['request']['item'], 2)

    def test_governance_guard_rejects_mutation_message_types(self) -> None:
        with self.assertRaises(PermissionError):
            self.router.send_message(
                sender='academy',
                receiver='workshop',
                message_type='world_mutation',
                session_id='session_blocked',
                payload={'target': 'gpu_time'},
            )


if __name__ == '__main__':
    unittest.main()
