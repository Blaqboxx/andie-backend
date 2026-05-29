
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .identity import IdentityProvider
from .models import DispatchEnvelope, Task


@dataclass
class DispatchEngine:
    def dispatch_task(self, task: Task, agent_name: str, identity: IdentityProvider) -> DispatchEnvelope:
        allowed, reason = identity.check_action('dispatch_task', {'task_id': task.task_id, 'agent_name': agent_name})
        if not allowed:
            raise PermissionError(reason)
        return DispatchEnvelope(
            envelope_id=f'dispatch_{uuid4().hex}',
            task_id=task.task_id,
            agent_name=agent_name,
            callback_channel=f'agent/{agent_name}/callback',
            payload={
                'task_id': task.task_id,
                'title': task.title,
                'description': task.description,
                'goal_id': task.goal_id,
            },
            metadata={'identity_check': reason},
        )
