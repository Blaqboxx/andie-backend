
from __future__ import annotations

from dataclasses import dataclass
from typing import List
from uuid import uuid4

from .models import Goal, Mission, ReflectionRecord, Task


@dataclass
class ReflectionEngine:
    def reflect(self, mission: Mission, goal: Goal, tasks: List[Task]) -> ReflectionRecord:
        completed = [task for task in tasks if task.status.value == 'completed']
        failed = [task for task in tasks if task.status.value == 'failed']
        lessons = []
        improvements = []
        if failed:
            lessons.append(f'{len(failed)} tasks failed and should be decomposed or guarded more tightly.')
            improvements.append('reduce task scope and tighten validation gates')
        else:
            lessons.append('execution closed cleanly with the current decomposition pattern.')
            improvements.append('promote this task template for similar missions')
        return ReflectionRecord(
            reflection_id=f'reflection_{uuid4().hex}',
            mission_id=mission.mission_id,
            goal_id=goal.goal_id,
            title=f'Reflection on {goal.title}',
            success=not failed and bool(completed),
            lessons=lessons,
            failures=[task.task_id for task in failed],
            improvements=improvements,
            metadata={'completed_count': len(completed), 'failed_count': len(failed)},
        )
