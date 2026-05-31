from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .controller import ExecutiveController
from .models import A2AMessage, A2AMessageStatus, utc_now


class LocalA2ARouter:
    def __init__(self, controller: ExecutiveController) -> None:
        self.controller = controller

    def _now(self) -> str:
        return utc_now()

    def _normalize_timeout_seconds(self, timeout_seconds: int | None) -> int:
        try:
            value = int(timeout_seconds if timeout_seconds is not None else 300)
        except Exception as exc:
            raise ValueError('timeout_seconds_invalid') from exc
        if value < 1 or value > 3600:
            raise ValueError('timeout_seconds_out_of_range')
        return value

    def _timeout_at(self, created_at: str, timeout_seconds: int) -> str:
        base = datetime.fromisoformat(str(created_at))
        return (base + timedelta(seconds=int(timeout_seconds))).astimezone(timezone.utc).isoformat()

    def _is_timed_out(self, message: A2AMessage) -> bool:
        if message.status != A2AMessageStatus.PENDING:
            return False
        timeout_at = str(message.timeout_at or '').strip()
        if not timeout_at:
            return False
        return datetime.now(timezone.utc) >= datetime.fromisoformat(timeout_at)

    def _mark_timed_out(self, message: A2AMessage) -> A2AMessage:
        if message.status != A2AMessageStatus.PENDING:
            return message
        message.status = A2AMessageStatus.TIMED_OUT
        message.updated_at = self._now()
        message.error_code = 'timeout'
        message.error_message = 'response_deadline_exceeded'
        self.controller.store.append_a2a_message(message)
        return message

    def _audit_rejection(
        self,
        *,
        sender: str,
        receiver: str,
        message_type: str,
        session_id: str,
        correlation_id: str,
        request: Dict[str, Any],
        error_code: str,
        error_message: str,
        timeout_seconds: int = 300,
    ) -> Dict[str, Any]:
        created_at = self._now()
        message = A2AMessage(
            message_id=f'a2a_{uuid4().hex}',
            correlation_id=(correlation_id or f'corr_{uuid4().hex}'),
            session_id=(session_id or 'session_unscoped'),
            sender=str(sender or '').strip().lower(),
            receiver=str(receiver or '').strip().lower(),
            created_at=created_at,
            updated_at=created_at,
            message_type=str(message_type or ''),
            request=dict(request or {}),
            response=None,
            status=A2AMessageStatus.REJECTED,
            timeout_seconds=self._normalize_timeout_seconds(timeout_seconds),
            timeout_at=self._timeout_at(created_at, self._normalize_timeout_seconds(timeout_seconds)),
            error_code=error_code,
            error_message=error_message,
        )
        self.controller.store.append_a2a_message(message)
        return message.to_dict()

    def _validate_message_type(self, message_type: str) -> None:
        lowered = str(message_type or '').strip().lower()
        if not lowered:
            raise ValueError('message_type_required')

        forbidden_tokens = {'world_mutation', 'mutate_world', 'direct_mutation', 'policy_override'}
        if lowered in forbidden_tokens or 'mutation' in lowered:
            raise PermissionError('a2a_message_type_forbidden_for_governance')

    def _validate_institutions(self, sender: str, receiver: str) -> None:
        if self.controller.store.get_institution_profile(sender) is None:
            raise ValueError(f'unknown_sender:{sender}')
        if self.controller.store.get_institution_profile(receiver) is None:
            raise ValueError(f'unknown_receiver:{receiver}')

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
        normalized_sender = str(sender or '').strip().lower()
        normalized_receiver = str(receiver or '').strip().lower()
        normalized_session_id = str(session_id or '').strip()
        normalized_correlation_id = str(correlation_id or '').strip()
        normalized_timeout_seconds = self._normalize_timeout_seconds(timeout_seconds)
        normalized_request = dict(payload or {})

        if not normalized_session_id:
            self._audit_rejection(
                sender=normalized_sender,
                receiver=normalized_receiver,
                message_type=message_type,
                session_id=normalized_session_id,
                correlation_id=normalized_correlation_id,
                request=normalized_request,
                error_code='session_id_required',
                error_message='session_id_required',
                timeout_seconds=normalized_timeout_seconds,
            )
            raise ValueError('session_id_required')
        if not normalized_correlation_id:
            self._audit_rejection(
                sender=normalized_sender,
                receiver=normalized_receiver,
                message_type=message_type,
                session_id=normalized_session_id,
                correlation_id=normalized_correlation_id,
                request=normalized_request,
                error_code='correlation_id_required',
                error_message='correlation_id_required',
                timeout_seconds=normalized_timeout_seconds,
            )
            raise ValueError('correlation_id_required')

        try:
            self._validate_institutions(normalized_sender, normalized_receiver)
        except ValueError as exc:
            self._audit_rejection(
                sender=normalized_sender,
                receiver=normalized_receiver,
                message_type=message_type,
                session_id=normalized_session_id,
                correlation_id=normalized_correlation_id,
                request=normalized_request,
                error_code='identity_failure',
                error_message=str(exc),
                timeout_seconds=normalized_timeout_seconds,
            )
            raise

        try:
            self._validate_message_type(message_type)
        except PermissionError as exc:
            self._audit_rejection(
                sender=normalized_sender,
                receiver=normalized_receiver,
                message_type=message_type,
                session_id=normalized_session_id,
                correlation_id=normalized_correlation_id,
                request=normalized_request,
                error_code='governance_rejection',
                error_message=str(exc),
                timeout_seconds=normalized_timeout_seconds,
            )
            raise
        except ValueError as exc:
            self._audit_rejection(
                sender=normalized_sender,
                receiver=normalized_receiver,
                message_type=message_type,
                session_id=normalized_session_id,
                correlation_id=normalized_correlation_id,
                request=normalized_request,
                error_code='message_validation_failure',
                error_message=str(exc),
                timeout_seconds=normalized_timeout_seconds,
            )
            raise

        allowed, reason = self.controller.identity.check_action(
            action='a2a:send_message',
            context={
                'sender': normalized_sender,
                'receiver': normalized_receiver,
                'message_type': str(message_type),
                'correlation_id': normalized_correlation_id,
                'session_id': normalized_session_id,
            },
        )
        if not allowed:
            self._audit_rejection(
                sender=normalized_sender,
                receiver=normalized_receiver,
                message_type=message_type,
                session_id=normalized_session_id,
                correlation_id=normalized_correlation_id,
                request=normalized_request,
                error_code='identity_failure',
                error_message=str(reason),
                timeout_seconds=normalized_timeout_seconds,
            )
            raise PermissionError(reason)

        created_at = self._now()
        message = A2AMessage(
            message_id=f'a2a_{uuid4().hex}',
            correlation_id=normalized_correlation_id,
            session_id=normalized_session_id,
            sender=normalized_sender,
            receiver=normalized_receiver,
            created_at=created_at,
            updated_at=created_at,
            message_type=str(message_type),
            request=normalized_request,
            response=None,
            status=A2AMessageStatus.PENDING,
            timeout_seconds=normalized_timeout_seconds,
            timeout_at=self._timeout_at(created_at, normalized_timeout_seconds),
            policy_decision_id=(str(policy_decision_id).strip() if policy_decision_id is not None else None),
            intent_id=(str(intent_id).strip() if intent_id is not None else None),
        )
        self.controller.store.append_a2a_message(message)
        return message.to_dict()

    def respond_message(self, message_id: str, response: Dict[str, Any]) -> Dict[str, Any]:
        message = self.controller.store.get_a2a_message(message_id)
        if message is None:
            raise ValueError('a2a_message_not_found')

        if message.status in {A2AMessageStatus.RESPONDED, A2AMessageStatus.REJECTED, A2AMessageStatus.TIMED_OUT}:
            raise ValueError('a2a_message_terminal_state')

        if self._is_timed_out(message):
            self._mark_timed_out(message)
            raise ValueError('a2a_message_timed_out')

        message.response = dict(response or {})
        message.status = A2AMessageStatus.RESPONDED
        message.updated_at = self._now()
        message.error_code = None
        message.error_message = None
        self.controller.store.append_a2a_message(message)
        return message.to_dict()

    def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        message = self.controller.store.get_a2a_message(message_id)
        if message is not None and self._is_timed_out(message):
            message = self._mark_timed_out(message)
        return message.to_dict() if message is not None else None

    def expire_timed_out_messages(self, *, session_id: Optional[str] = None) -> int:
        messages = self.controller.store.list_a2a_messages(session_id=session_id)
        changed = 0
        for item in messages:
            if self._is_timed_out(item):
                self._mark_timed_out(item)
                changed += 1
        return changed

    def list_session_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        self.expire_timed_out_messages(session_id=str(session_id))
        messages = self.controller.store.list_a2a_messages(session_id=str(session_id))
        return [item.to_dict() for item in messages[-normalized_limit:]]

    def inbox(self, receiver: str, *, session_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        self.expire_timed_out_messages(session_id=(str(session_id) if session_id is not None else None))
        messages = self.controller.store.list_a2a_messages(
            receiver=str(receiver or '').strip().lower(),
            session_id=(str(session_id) if session_id is not None else None),
        )
        return [item.to_dict() for item in messages[-normalized_limit:]]

    def run_research_prototype_workflow(self, *, session_id: str, topic: str) -> Dict[str, Any]:
        normalized_session_id = str(session_id or '').strip()
        normalized_topic = str(topic or '').strip()
        if not normalized_session_id:
            raise ValueError('session_id_required')
        if not normalized_topic:
            raise ValueError('topic_required')

        # Step 1: Academy requests research/prototype support from Workshop.
        request_message = self.send_message(
            sender='academy',
            receiver='workshop',
            message_type='research_request',
            payload={'topic': normalized_topic},
            session_id=normalized_session_id,
            correlation_id=f'corr_{uuid4().hex}',
        )

        request_response = self.respond_message(
            request_message['message_id'],
            {
                'status': 'accepted',
                'next_action': 'prototype_result',
            },
        )

        # Step 2: Workshop sends a prototype result back to Academy.
        result_message = self.send_message(
            sender='workshop',
            receiver='academy',
            message_type='prototype_result',
            payload={
                'topic': normalized_topic,
                'artifact': f'prototype_{normalized_topic.replace(" ", "_").lower()}',
                'confidence': 'initial',
            },
            session_id=normalized_session_id,
            correlation_id=request_message['correlation_id'],
        )

        result_response = self.respond_message(
            result_message['message_id'],
            {
                'status': 'received',
                'next_action': 'executive_review',
            },
        )

        session_messages = self.list_session_messages(normalized_session_id, limit=100)
        return {
            'session_id': normalized_session_id,
            'workflow_type': 'research_prototype',
            'topic': normalized_topic,
            'steps': [
                {'message_id': request_message['message_id'], 'status': request_response['status']},
                {'message_id': result_message['message_id'], 'status': result_response['status']},
            ],
            'message_count': len(session_messages),
            'completed': True,
        }

    def replay_workflow_exchange(self, *, session_id: str, correlation_id: str, limit: int = 100) -> Dict[str, Any]:
        normalized_session_id = str(session_id or '').strip()
        normalized_correlation_id = str(correlation_id or '').strip()
        normalized_limit = max(1, min(int(limit), 500))
        messages = [
            item
            for item in self.controller.store.list_a2a_messages(session_id=normalized_session_id)
            if item.correlation_id == normalized_correlation_id
        ]
        selected = messages[-normalized_limit:]
        return {
            'found': bool(selected),
            'session_id': normalized_session_id,
            'correlation_id': normalized_correlation_id,
            'count': len(selected),
            'items': [item.to_dict() for item in selected],
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
        normalized_session_id = str(session_id or '').strip()
        normalized_topic = str(topic or '').strip()
        normalized_request_type = str(request_type or '').strip()
        normalized_response_type = str(response_type or '').strip()
        normalized_correlation_id = f'corr_{uuid4().hex}'
        normalized_timeout_seconds = self._normalize_timeout_seconds(timeout_seconds)

        if not normalized_session_id:
            raise ValueError('session_id_required')
        if not normalized_topic:
            raise ValueError('topic_required')

        request_payload = {
            'topic': normalized_topic,
            'workflow': 'workshop_academy_exchange',
            'request_type': normalized_request_type,
        }
        try:
            request_message = self.send_message(
                sender='workshop',
                receiver='academy',
                message_type=normalized_request_type,
                payload=request_payload,
                session_id=normalized_session_id,
                correlation_id=normalized_correlation_id,
                timeout_seconds=normalized_timeout_seconds,
                intent_id='workflow:institution_exchange',
            )
        except (PermissionError, ValueError) as exc:
            replay = self.replay_workflow_exchange(session_id=normalized_session_id, correlation_id=normalized_correlation_id)
            return {
                'session_id': normalized_session_id,
                'correlation_id': normalized_correlation_id,
                'workflow_type': 'workshop_academy_exchange',
                'request_type': normalized_request_type,
                'response_type': normalized_response_type,
                'status': 'rejected',
                'failure_stage': 'request',
                'error': str(exc),
                'completed': False,
                'steps': [],
                'message_count': replay['count'],
                'replay': replay,
            }

        steps = [
            {
                'stage': 'request',
                'message_id': request_message['message_id'],
                'message_type': request_message['message_type'],
                'status': request_message['status'],
            },
        ]

        if simulate_timeout:
            request_record = self.controller.store.get_a2a_message(request_message['message_id'])
            if request_record is not None:
                request_record.timeout_at = '1970-01-01T00:00:00+00:00'
                self.controller.store.append_a2a_message(request_record)
            self.expire_timed_out_messages(session_id=normalized_session_id)
            timed_out_message = self.get_message(request_message['message_id']) or request_message
            steps[0]['status'] = timed_out_message['status']
            replay = self.replay_workflow_exchange(session_id=normalized_session_id, correlation_id=normalized_correlation_id)
            return {
                'session_id': normalized_session_id,
                'correlation_id': normalized_correlation_id,
                'workflow_type': 'workshop_academy_exchange',
                'request_type': normalized_request_type,
                'response_type': normalized_response_type,
                'status': 'timed_out',
                'failure_stage': 'request',
                'completed': False,
                'steps': steps,
                'message_count': replay['count'],
                'replay': replay,
            }

        response_payload = {
            'topic': normalized_topic,
            'workflow': 'workshop_academy_exchange',
            'response_type': normalized_response_type,
            'finding': f'governed_result_for_{normalized_topic.replace(" ", "_").lower()}',
            'next_action': 'workshop_review',
        }
        response_message = self.send_message(
            sender='academy',
            receiver='workshop',
            message_type=normalized_response_type,
            payload=response_payload,
            session_id=normalized_session_id,
            correlation_id=normalized_correlation_id,
            timeout_seconds=normalized_timeout_seconds,
            intent_id='workflow:institution_exchange',
        )

        request_ack = self.respond_message(
            request_message['message_id'],
            {
                'status': 'accepted',
                'next_action': normalized_response_type,
            },
        )
        response_ack = self.respond_message(
            response_message['message_id'],
            {
                'status': 'received',
                'next_action': 'workflow_complete',
            },
        )

        steps.append(
            {
                'stage': 'response',
                'message_id': response_message['message_id'],
                'message_type': response_message['message_type'],
                'status': response_ack['status'],
            }
        )

        replay = self.replay_workflow_exchange(session_id=normalized_session_id, correlation_id=normalized_correlation_id)
        return {
            'session_id': normalized_session_id,
            'correlation_id': normalized_correlation_id,
            'workflow_type': 'workshop_academy_exchange',
            'request_type': normalized_request_type,
            'response_type': normalized_response_type,
            'status': 'completed',
            'failure_stage': None,
            'completed': True,
            'steps': steps,
            'request_ack': request_ack,
            'response_ack': response_ack,
            'message_count': replay['count'],
            'replay': replay,
        }

    def run_workshop_academy_inference_workflow(
        self,
        *,
        session_id: str,
        topic: str,
        timeout_seconds: int = 300,
    ) -> Dict[str, Any]:
        normalized_session_id = str(session_id or '').strip()
        normalized_topic = str(topic or '').strip()
        normalized_correlation_id = f'corr_{uuid4().hex}'
        normalized_timeout_seconds = self._normalize_timeout_seconds(timeout_seconds)

        if not normalized_session_id:
            raise ValueError('session_id_required')
        if not normalized_topic:
            raise ValueError('topic_required')

        request_message = self.send_message(
            sender='workshop',
            receiver='academy',
            message_type='research_request',
            payload={
                'topic': normalized_topic,
                'workflow': 'workshop_academy_inference_exchange',
                'request_type': 'research_request',
            },
            session_id=normalized_session_id,
            correlation_id=normalized_correlation_id,
            timeout_seconds=normalized_timeout_seconds,
            intent_id='workflow:institution_exchange',
        )

        inference_message = self.send_message(
            sender='academy',
            receiver='inference',
            message_type='inference_request',
            payload={
                'topic': normalized_topic,
                'workflow': 'workshop_academy_inference_exchange',
                'request_type': 'inference_request',
            },
            session_id=normalized_session_id,
            correlation_id=normalized_correlation_id,
            timeout_seconds=normalized_timeout_seconds,
            intent_id='workflow:institution_exchange',
        )

        request_ack = self.respond_message(
            request_message['message_id'],
            {
                'status': 'accepted',
                'next_action': 'inference_request',
            },
        )
        inference_ack = self.respond_message(
            inference_message['message_id'],
            {
                'status': 'received',
                'next_action': 'workflow_complete',
            },
        )

        replay = self.replay_workflow_exchange(session_id=normalized_session_id, correlation_id=normalized_correlation_id)
        return {
            'session_id': normalized_session_id,
            'correlation_id': normalized_correlation_id,
            'workflow_type': 'workshop_academy_inference_exchange',
            'status': 'completed',
            'failure_stage': None,
            'completed': True,
            'steps': [
                {
                    'stage': 'workshop_to_academy',
                    'message_id': request_message['message_id'],
                    'message_type': request_message['message_type'],
                    'status': request_ack['status'],
                },
                {
                    'stage': 'academy_to_inference',
                    'message_id': inference_message['message_id'],
                    'message_type': inference_message['message_type'],
                    'status': inference_ack['status'],
                },
            ],
            'request_ack': request_ack,
            'inference_ack': inference_ack,
            'message_count': replay['count'],
            'replay': replay,
        }
