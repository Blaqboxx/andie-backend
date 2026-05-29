
from __future__ import annotations

from dataclasses import dataclass
from typing import List
from uuid import uuid4

from .identity import IdentityProvider
from .models import Goal, Mission, Task


@dataclass
class PlanningEngine:
    default_agent: str = 'general'

    def generate_plan(self, mission: Mission, goal: Goal, identity: IdentityProvider) -> List[Task]:
        mission_statement = identity.mission()
        plan_fragments = [
            ('research', f'Research the objective for {goal.title}'),
            ('architecture', f'Design the execution path for {goal.title}'),
            ('implementation', f'Implement the core changes for {goal.title}'),
            ('validation', f'Validate the result for {goal.title}'),
        ]
        tasks: List[Task] = []
        for index, (phase, title) in enumerate(plan_fragments, start=1):
            task = Task(
                task_id=f'task_{uuid4().hex}',
                goal_id=goal.goal_id,
                title=f'{index}. {title}',
                description=f'Mission: {mission.title}. Identity mission: {mission_statement}. Phase: {phase}.',
                priority=goal.priority,
                agent=self.default_agent,
                dependencies=[tasks[-1].task_id] if tasks else [],
                inputs={'phase': phase, 'goal': goal.to_dict(), 'mission': mission.to_dict()},
                metadata={'phase': phase, 'generated_by': 'planner'},
            )
            tasks.append(task)
        return tasks
