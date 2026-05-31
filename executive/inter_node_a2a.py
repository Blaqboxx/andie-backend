from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx

from .a2a import LocalA2ARouter


class HttpA2ATransportClient:
    def __init__(self, node_endpoints: Dict[str, str], timeout_seconds: float = 10.0) -> None:
        self.node_endpoints = {str(k): str(v).rstrip('/') for k, v in dict(node_endpoints or {}).items()}
        self.timeout_seconds = float(timeout_seconds)

    def _endpoint(self, node_id: str) -> str:
        endpoint = self.node_endpoints.get(str(node_id))
        if not endpoint:
            raise ValueError(f'unknown_node_endpoint:{node_id}')
        return endpoint

    def send_message(self, *, node_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        base_url = self._endpoint(node_id)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f'{base_url}/a2a/messages', json=dict(payload or {}))
        if response.status_code >= 400:
            raise ValueError(f'transport_send_failed:{node_id}:{response.status_code}')
        body = response.json()
        return dict(body.get('message') or {})

    def respond_message(self, *, node_id: str, message_id: str, response_payload: Dict[str, Any]) -> Dict[str, Any]:
        base_url = self._endpoint(node_id)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f'{base_url}/a2a/messages/{message_id}/response',
                json={'response': dict(response_payload or {})},
            )
        if response.status_code >= 400:
            raise ValueError(f'transport_respond_failed:{node_id}:{response.status_code}')
        body = response.json()
        return dict(body.get('message') or {})

    def get_message(self, *, node_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        base_url = self._endpoint(node_id)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f'{base_url}/a2a/messages/{message_id}')
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ValueError(f'transport_get_failed:{node_id}:{response.status_code}')
        body = response.json()
        return dict(body.get('message') or {})

    def list_session_messages(self, *, node_id: str, session_id: str, limit: int = 500) -> List[Dict[str, Any]]:
        base_url = self._endpoint(node_id)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f'{base_url}/a2a/sessions/{session_id}/messages?limit={int(limit)}')
        if response.status_code >= 400:
            raise ValueError(f'transport_list_failed:{node_id}:{response.status_code}')
        body = response.json()
        return [dict(item) for item in list(body.get('items') or []) if isinstance(item, dict)]


class InterNodeA2ARouter:
    def __init__(
        self,
        *,
        local_router: LocalA2ARouter,
        local_node_id: str,
        institution_nodes: Dict[str, str],
        transport_client: HttpA2ATransportClient,
    ) -> None:
        self.local_router = local_router
        self.local_node_id = str(local_node_id or 'local')
        self.institution_nodes = {str(k).strip().lower(): str(v).strip() for k, v in dict(institution_nodes or {}).items()}
        self.transport_client = transport_client
        self._message_node_index: Dict[str, str] = {}

    def _node_for_institution(self, institution_id: str) -> str:
        normalized = str(institution_id or '').strip().lower()
        return self.institution_nodes.get(normalized, self.local_node_id)

    def _with_transport_metadata(self, message: Dict[str, Any], *, source_node: str, target_node: str) -> Dict[str, Any]:
        enriched = dict(message or {})
        enriched['transport'] = {
            'mode': ('local' if source_node == target_node else 'inter_node'),
            'source_node': source_node,
            'target_node': target_node,
        }
        return enriched

    def send_message(
        self,
        *,
        sender: str,
        receiver: str,
        message_type: str,
        payload: Dict[str, Any],
        session_id: str,
        correlation_id: str,
        timeout_seconds: int = 300,
        policy_decision_id: Optional[str] = None,
        intent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_node = self._node_for_institution(sender)
        target_node = self._node_for_institution(receiver)
        outbound = {
            'sender': str(sender or '').strip().lower(),
            'receiver': str(receiver or '').strip().lower(),
            'message_type': str(message_type or ''),
            'session_id': str(session_id or '').strip(),
            'correlation_id': str(correlation_id or '').strip(),
            'payload': dict(payload or {}),
            'timeout_seconds': int(timeout_seconds),
            'policy_decision_id': policy_decision_id,
            'intent_id': intent_id,
        }

        if target_node == self.local_node_id:
            message = self.local_router.send_message(
                sender=outbound['sender'],
                receiver=outbound['receiver'],
                message_type=outbound['message_type'],
                payload=outbound['payload'],
                session_id=outbound['session_id'],
                correlation_id=outbound['correlation_id'],
                timeout_seconds=outbound['timeout_seconds'],
                policy_decision_id=policy_decision_id,
                intent_id=intent_id,
            )
        else:
            message = self.transport_client.send_message(node_id=target_node, payload=outbound)

        message_id = str(message.get('message_id', '')).strip()
        if message_id:
            self._message_node_index[message_id] = target_node
        return self._with_transport_metadata(message, source_node=source_node, target_node=target_node)

    def respond_message(self, message_id: str, response: Dict[str, Any]) -> Dict[str, Any]:
        node_id = self._message_node_index.get(str(message_id), self.local_node_id)
        if node_id == self.local_node_id:
            message = self.local_router.respond_message(message_id, response)
            return self._with_transport_metadata(message, source_node=self.local_node_id, target_node=self.local_node_id)
        message = self.transport_client.respond_message(node_id=node_id, message_id=message_id, response_payload=response)
        return self._with_transport_metadata(message, source_node=self.local_node_id, target_node=node_id)

    def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        node_id = self._message_node_index.get(str(message_id), self.local_node_id)
        if node_id == self.local_node_id:
            message = self.local_router.get_message(message_id)
            if message is None:
                return None
            return self._with_transport_metadata(message, source_node=self.local_node_id, target_node=self.local_node_id)
        message = self.transport_client.get_message(node_id=node_id, message_id=message_id)
        if message is None:
            return None
        return self._with_transport_metadata(message, source_node=self.local_node_id, target_node=node_id)

    def _list_from_known_nodes(self, session_id: str, limit: int) -> List[Dict[str, Any]]:
        local_items = [self._with_transport_metadata(item, source_node=self.local_node_id, target_node=self.local_node_id) for item in self.local_router.list_session_messages(session_id, limit=limit)]
        remote_nodes = sorted({node for node in self.institution_nodes.values() if node and node != self.local_node_id})
        all_items = list(local_items)
        for node_id in remote_nodes:
            try:
                remote_items = self.transport_client.list_session_messages(node_id=node_id, session_id=session_id, limit=limit)
            except ValueError:
                continue
            for item in remote_items:
                enriched = self._with_transport_metadata(item, source_node=self.local_node_id, target_node=node_id)
                message_id = str(enriched.get('message_id', '')).strip()
                if message_id:
                    self._message_node_index[message_id] = node_id
                all_items.append(enriched)

        dedup: Dict[str, Dict[str, Any]] = {}
        for item in all_items:
            message_id = str(item.get('message_id', '')).strip()
            if message_id:
                dedup[message_id] = item
        items = list(dedup.values())
        items.sort(key=lambda item: str(item.get('created_at') or item.get('timestamp') or ''))
        return items[-max(1, min(int(limit), 500)):]

    def list_session_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self._list_from_known_nodes(str(session_id), limit)

    def inbox(self, receiver: str, *, session_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if session_id is None:
            # Keep parity with local inbox semantics for broad queries.
            return self.local_router.inbox(receiver=receiver, session_id=None, limit=limit)
        items = self._list_from_known_nodes(str(session_id or ''), limit)
        normalized_receiver = str(receiver or '').strip().lower()
        selected = [item for item in items if str(item.get('receiver', '')).strip().lower() == normalized_receiver]
        if session_id:
            normalized_session_id = str(session_id).strip()
            selected = [item for item in selected if str(item.get('session_id', '')).strip() == normalized_session_id]
        return selected[-max(1, min(int(limit), 500)):]

    def replay_workflow_exchange(self, *, session_id: str, correlation_id: str, limit: int = 100) -> Dict[str, Any]:
        normalized_session_id = str(session_id or '').strip()
        normalized_correlation_id = str(correlation_id or '').strip()
        items = [
            item
            for item in self.list_session_messages(normalized_session_id, limit=max(100, int(limit)))
            if str(item.get('correlation_id', '')).strip() == normalized_correlation_id
        ]
        selected = items[-max(1, min(int(limit), 500)):]
        return {
            'found': bool(selected),
            'session_id': normalized_session_id,
            'correlation_id': normalized_correlation_id,
            'count': len(selected),
            'items': selected,
        }

    def run_workshop_academy_workflow(
        self,
        *,
        session_id: str,
        topic: str,
        request_type: str = 'research_request',
        response_type: str = 'research_result',
        timeout_seconds: int = 300,
        simulate_timeout: bool = False,
    ) -> Dict[str, Any]:
        # Reuse the established workflow semantics and only vary transport location.
        if simulate_timeout:
            return self.local_router.run_workshop_academy_workflow(
                session_id=session_id,
                topic=topic,
                request_type=request_type,
                response_type=response_type,
                timeout_seconds=timeout_seconds,
                simulate_timeout=True,
            )

        correlation_id = f'corr_{uuid4().hex}'
        request_message = self.send_message(
            sender='workshop',
            receiver='academy',
            message_type=request_type,
            payload={
                'topic': str(topic or '').strip(),
                'workflow': 'workshop_academy_exchange',
                'request_type': str(request_type or '').strip(),
            },
            session_id=str(session_id or '').strip(),
            correlation_id=correlation_id,
            timeout_seconds=timeout_seconds,
            intent_id='workflow:institution_exchange',
        )

        response_message = self.send_message(
            sender='academy',
            receiver='workshop',
            message_type=response_type,
            payload={
                'topic': str(topic or '').strip(),
                'workflow': 'workshop_academy_exchange',
                'response_type': str(response_type or '').strip(),
                'finding': f'governed_result_for_{str(topic or "").strip().replace(" ", "_").lower()}',
                'next_action': 'workshop_review',
            },
            session_id=str(session_id or '').strip(),
            correlation_id=correlation_id,
            timeout_seconds=timeout_seconds,
            intent_id='workflow:institution_exchange',
        )

        self.respond_message(request_message['message_id'], {'status': 'accepted', 'next_action': response_type})
        self.respond_message(response_message['message_id'], {'status': 'received', 'next_action': 'workflow_complete'})

        replay = self.replay_workflow_exchange(session_id=str(session_id or '').strip(), correlation_id=correlation_id)
        return {
            'session_id': str(session_id or '').strip(),
            'correlation_id': correlation_id,
            'workflow_type': 'workshop_academy_exchange',
            'request_type': str(request_type or '').strip(),
            'response_type': str(response_type or '').strip(),
            'status': 'completed',
            'failure_stage': None,
            'completed': True,
            'steps': [
                {
                    'stage': 'request',
                    'message_id': request_message['message_id'],
                    'message_type': request_message['message_type'],
                    'status': 'responded',
                },
                {
                    'stage': 'response',
                    'message_id': response_message['message_id'],
                    'message_type': response_message['message_type'],
                    'status': 'responded',
                },
            ],
            'message_count': replay['count'],
            'replay': replay,
        }
