from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from .controller import ExecutiveController
from .models import A2AMessage, A2AMessageStatus, utc_now


class LocalA2ARouter:
    def __init__(self, controller: ExecutiveController) -> None:
        self.controller = controller

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
    ) -> Dict[str, Any]:
        normalized_sender = str(sender or '').strip().lower()
        normalized_receiver = str(receiver or '').strip().lower()
        normalized_session_id = str(session_id or '').strip()

        if not normalized_session_id:
            raise ValueError('session_id_required')

        self._validate_institutions(normalized_sender, normalized_receiver)
        self._validate_message_type(message_type)

        allowed, reason = self.controller.identity.check_action(
            action='a2a:send_message',
            context={
                'sender': normalized_sender,
                'receiver': normalized_receiver,
                'message_type': str(message_type),
                'session_id': normalized_session_id,
            },
        )
        if not allowed:
            raise PermissionError(reason)

        message = A2AMessage(
            message_id=f'a2a_{uuid4().hex}',
            session_id=normalized_session_id,
            sender=normalized_sender,
            receiver=normalized_receiver,
            timestamp=utc_now(),
            message_type=str(message_type),
            request=dict(payload or {}),
            response={},
            status=A2AMessageStatus.DELIVERED,
        )
        self.controller.store.append_a2a_message(message)
        return message.to_dict()

    def respond_message(self, message_id: str, response: Dict[str, Any]) -> Dict[str, Any]:
        message = self.controller.store.get_a2a_message(message_id)
        if message is None:
            raise ValueError('a2a_message_not_found')

        message.response = dict(response or {})
        message.status = A2AMessageStatus.RESPONDED
        self.controller.store.append_a2a_message(message)
        return message.to_dict()

    def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        message = self.controller.store.get_a2a_message(message_id)
        return message.to_dict() if message is not None else None

    def list_session_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        messages = self.controller.store.list_a2a_messages(session_id=str(session_id))
        return [item.to_dict() for item in messages[-normalized_limit:]]

    def inbox(self, receiver: str, *, session_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        messages = self.controller.store.list_a2a_messages(
            receiver=str(receiver or '').strip().lower(),
            session_id=(str(session_id) if session_id is not None else None),
        )
        return [item.to_dict() for item in messages[-normalized_limit:]]
