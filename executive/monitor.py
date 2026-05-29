
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .models import Goal, Task, TaskStatus


@dataclass
class MonitoringEngine:
    def summarize(self, goal: Goal, tasks: List[Task]) -> Dict[str, object]:
        completed = sum(1 for task in tasks if task.status == TaskStatus.COMPLETED)
        failed = sum(1 for task in tasks if task.status == TaskStatus.FAILED)
        pending = sum(1 for task in tasks if task.status in {TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.WAITING})
        return {
            'goal_id': goal.goal_id,
            'goal_status': goal.status.value,
            'completed': completed,
            'failed': failed,
            'pending': pending,
            'all_complete': bool(tasks) and completed == len(tasks),
        }
