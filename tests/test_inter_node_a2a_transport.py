import tempfile
import unittest
from pathlib import Path

from executive.a2a import LocalA2ARouter
from executive.controller import ExecutiveController
from executive.inter_node_a2a import InterNodeA2ARouter
from executive.models import ExecutiveConfig


class _FakeTransportClient:
    def __init__(self, routers, fail_send_attempts=None, always_fail_nodes=None):
        self.routers = dict(routers)
        self.fail_send_attempts = {str(k): int(v) for k, v in dict(fail_send_attempts or {}).items()}
        self.always_fail_nodes = {str(item) for item in list(always_fail_nodes or [])}
        self.send_attempt_counts = {}

    def send_message(self, *, node_id, payload):
        normalized_node_id = str(node_id)
        self.send_attempt_counts[normalized_node_id] = self.send_attempt_counts.get(normalized_node_id, 0) + 1
        if normalized_node_id in self.always_fail_nodes:
            raise ValueError(f'transport_send_failed:{normalized_node_id}:503')
        if self.fail_send_attempts.get(normalized_node_id, 0) > 0:
            self.fail_send_attempts[normalized_node_id] -= 1
            raise ValueError(f'transport_send_failed:{normalized_node_id}:503')

        router = self.routers[normalized_node_id]
        return router.send_message(
            sender=payload['sender'],
            receiver=payload['receiver'],
            message_type=payload['message_type'],
            payload=payload.get('payload', {}),
            session_id=payload['session_id'],
            correlation_id=payload['correlation_id'],
            timeout_seconds=payload.get('timeout_seconds', 300),
            policy_decision_id=payload.get('policy_decision_id'),
            intent_id=payload.get('intent_id'),
        )

    def respond_message(self, *, node_id, message_id, response_payload):
        router = self.routers[str(node_id)]
        return router.respond_message(message_id, response_payload)

    def get_message(self, *, node_id, message_id):
        router = self.routers[str(node_id)]
        return router.get_message(message_id)

    def list_session_messages(self, *, node_id, session_id, limit=500):
        router = self.routers[str(node_id)]
        return router.list_session_messages(session_id=session_id, limit=limit)


class InterNodeTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        node1_store = Path(self.tmpdir.name) / 'node1_state.json'
        node2_store = Path(self.tmpdir.name) / 'node2_state.json'
        local_store = Path(self.tmpdir.name) / 'local_state.json'

        self.node1_controller = ExecutiveController(
            config=ExecutiveConfig(store_path=str(node1_store), simulate_execution=True)
        )
        self.node2_controller = ExecutiveController(
            config=ExecutiveConfig(store_path=str(node2_store), simulate_execution=True)
        )
        self.local_controller = ExecutiveController(
            config=ExecutiveConfig(store_path=str(local_store), simulate_execution=True)
        )

        self.node1_local_router = LocalA2ARouter(self.node1_controller)
        self.node2_local_router = LocalA2ARouter(self.node2_controller)
        self.baseline_local_router = LocalA2ARouter(self.local_controller)

        fake_transport = _FakeTransportClient(
            {
                'blaqtower1': self.node1_local_router,
                'blaqtower2': self.node2_local_router,
            }
        )
        self.inter_node_router = InterNodeA2ARouter(
            local_router=self.node2_local_router,
            local_node_id='blaqtower2',
            institution_nodes={'workshop': 'blaqtower2', 'academy': 'blaqtower1'},
            transport_client=fake_transport,
        )

    @staticmethod
    def _semantic_projection(items):
        projected = []
        for item in items:
            projected.append(
                {
                    'sender': item['sender'],
                    'receiver': item['receiver'],
                    'message_type': item['message_type'],
                    'status': item['status'],
                    'request': dict(item.get('request') or {}),
                    'response': dict(item.get('response') or {}),
                }
            )
        return projected

    def test_inter_node_workflow_matches_local_semantics(self) -> None:
        local = self.baseline_local_router.run_workshop_academy_workflow(
            session_id='session_local_equivalence',
            topic='routing strategy',
        )
        inter = self.inter_node_router.run_workshop_academy_workflow(
            session_id='session_inter_equivalence',
            topic='routing strategy',
        )

        self.assertTrue(local['completed'])
        self.assertTrue(inter['completed'])
        self.assertEqual(local['status'], 'completed')
        self.assertEqual(inter['status'], 'completed')
        self.assertEqual(local['message_count'], 2)
        self.assertEqual(inter['message_count'], 2)

        local_replay = local['replay']['items']
        inter_replay = inter['replay']['items']

        self.assertEqual(len(local_replay), 2)
        self.assertEqual(len(inter_replay), 2)

        self.assertEqual([item['sender'] for item in local_replay], [item['sender'] for item in inter_replay])
        self.assertEqual([item['receiver'] for item in local_replay], [item['receiver'] for item in inter_replay])
        self.assertEqual([item['message_type'] for item in local_replay], [item['message_type'] for item in inter_replay])
        self.assertEqual([item['status'] for item in local_replay], [item['status'] for item in inter_replay])
        self.assertEqual(self._semantic_projection(local_replay), self._semantic_projection(inter_replay))
        self.assertEqual([item['session_id'] for item in inter_replay], ['session_inter_equivalence', 'session_inter_equivalence'])
        self.assertEqual(inter_replay[0]['correlation_id'], inter_replay[1]['correlation_id'])

        # Cross-node replay includes transport metadata while keeping workflow semantics unchanged.
        self.assertIn('transport', inter_replay[0])
        self.assertIn('transport', inter_replay[1])
        self.assertEqual(inter_replay[0]['transport']['target_node'], 'blaqtower1')
        self.assertEqual(inter_replay[1]['transport']['target_node'], 'blaqtower2')

    def test_retry_determinism_produces_single_remote_outcome(self) -> None:
        flaky_transport = _FakeTransportClient(
            {
                'blaqtower1': self.node1_local_router,
                'blaqtower2': self.node2_local_router,
            },
            fail_send_attempts={'blaqtower1': 1},
        )
        router = InterNodeA2ARouter(
            local_router=self.node2_local_router,
            local_node_id='blaqtower2',
            institution_nodes={'workshop': 'blaqtower2', 'academy': 'blaqtower1'},
            transport_client=flaky_transport,
            transport_retry_limit=2,
        )

        workflow = router.run_workshop_academy_workflow(
            session_id='session_retry_determinism',
            topic='retry behavior',
        )
        self.assertTrue(workflow['completed'])
        self.assertEqual(workflow['status'], 'completed')
        self.assertEqual(flaky_transport.send_attempt_counts.get('blaqtower1'), 2)

        remote_messages = self.node1_local_router.list_session_messages('session_retry_determinism', limit=20)
        self.assertEqual(len(remote_messages), 1)
        self.assertEqual(remote_messages[0]['receiver'], 'academy')

    def test_node_outage_returns_deterministic_timeout_and_audit(self) -> None:
        down_transport = _FakeTransportClient(
            {
                'blaqtower1': self.node1_local_router,
                'blaqtower2': self.node2_local_router,
            },
            always_fail_nodes={'blaqtower1'},
        )
        router = InterNodeA2ARouter(
            local_router=self.node2_local_router,
            local_node_id='blaqtower2',
            institution_nodes={'workshop': 'blaqtower2', 'academy': 'blaqtower1'},
            transport_client=down_transport,
            transport_retry_limit=2,
        )

        workflow = router.run_workshop_academy_workflow(
            session_id='session_outage_timeout',
            topic='academy down test',
        )
        self.assertFalse(workflow['completed'])
        self.assertEqual(workflow['status'], 'timed_out')
        self.assertEqual(workflow['failure_stage'], 'request')

        replay = workflow['replay']
        self.assertTrue(replay['found'])
        self.assertEqual(replay['count'], 1)
        self.assertEqual(replay['items'][0]['status'], 'timed_out')
        self.assertEqual(replay['items'][0]['error_code'], 'retry_exhausted')


if __name__ == '__main__':
    unittest.main()
