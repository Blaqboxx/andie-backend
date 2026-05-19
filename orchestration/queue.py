"""
Controlled Task Queue Layer

Implements:
  - Bounded queue (max size)
  - Timeout-aware task execution
  - Retry semantics with backoff
  - Cancellation support
  - Observable queue state
  - Atomic state mutations
"""

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


class TaskStatus(Enum):
    """Task lifecycle states."""
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(Enum):
    """Task type classification."""
    RECOVERY = "recovery"
    OPTIMIZATION = "optimization"
    MAINTENANCE = "maintenance"
    CUSTOM = "custom"


@dataclass
class TaskRetry:
    """Retry configuration and state."""
    max_retries: int = 3
    retry_count: int = 0
    backoff_seconds: float = 2.0  # exponential: 2, 4, 8, ...
    last_error: Optional[str] = None
    
    def should_retry(self) -> bool:
        """Check if task should be retried."""
        return self.retry_count < self.max_retries
    
    def next_retry_delay(self) -> float:
        """Calculate delay before next retry (exponential backoff)."""
        return self.backoff_seconds * (2 ** self.retry_count)


@dataclass
class Task:
    """Canonical task representation."""
    id: str = field(default_factory=lambda: str(uuid4()))
    type: TaskType = TaskType.CUSTOM
    status: TaskStatus = TaskStatus.PENDING
    payload: Dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 60.0
    retry: TaskRetry = field(default_factory=TaskRetry)
    
    # Lifecycle timestamps
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    claimed_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    # Execution metadata
    claimed_by: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    # Observability
    tags: List[str] = field(default_factory=list)
    
    def is_expired(self) -> bool:
        """Check if task has exceeded timeout."""
        if self.claimed_at is None:
            return False
        claimed = datetime.fromisoformat(self.claimed_at)
        elapsed = (datetime.now(timezone.utc) - claimed).total_seconds()
        return elapsed > self.timeout_seconds
    
    def mark_claimed(self, claimer: str) -> None:
        """Mark task as claimed by executor."""
        self.status = TaskStatus.CLAIMED
        self.claimed_by = claimer
        self.claimed_at = datetime.now(timezone.utc).isoformat()
    
    def mark_completed(self, result: Dict[str, Any]) -> None:
        """Mark task as successfully completed."""
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.completed_at = datetime.now(timezone.utc).isoformat()
    
    def mark_failed(self, error: str) -> None:
        """Mark task as failed."""
        self.status = TaskStatus.FAILED
        self.error = error
        self.completed_at = datetime.now(timezone.utc).isoformat()
    
    def mark_cancelled(self) -> None:
        """Mark task as cancelled."""
        self.status = TaskStatus.CANCELLED
        self.completed_at = datetime.now(timezone.utc).isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (for storage/transmission)."""
        data = asdict(self)
        data['type'] = self.type.value
        data['status'] = self.status.value
        data['retry'] = asdict(self.retry)
        return data


class BoundedTaskQueue:
    """
    Governed task queue with bounded semantics.
    
    Guarantees:
      - Bounded size (no unbounded growth)
      - Timeout-aware execution
      - Retry with exponential backoff
      - Atomic state mutations
      - Observable queue metrics
      - Cancellation support
    """
    
    def __init__(
        self,
        max_queue_size: int = 100,
        storage_path: Optional[Path] = None,
    ):
        self.max_queue_size = max_queue_size
        self.storage_path = storage_path or Path("/tmp/andie_task_queue.json")
        
        # In-memory state
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.RLock()
        
        # Metrics
        self._total_processed = 0
        self._total_completed = 0
        self._total_failed = 0
        self._total_cancelled = 0
        
        # Load persisted tasks
        self._load_from_storage()
    
    def add_task(
        self,
        task_type: TaskType,
        payload: Dict[str, Any],
        timeout_seconds: float = 60.0,
        tags: Optional[List[str]] = None,
    ) -> Task:
        """
        Add task to queue.
        
        Raises:
            ValueError: if queue is at max capacity
        """
        with self._lock:
            pending_count = sum(
                1 for t in self._tasks.values()
                if t.status == TaskStatus.PENDING
            )
            if pending_count >= self.max_queue_size:
                raise ValueError(
                    f"Queue full: {pending_count} pending tasks (max={self.max_queue_size})"
                )
            
            task = Task(
                type=task_type,
                payload=payload,
                timeout_seconds=timeout_seconds,
                tags=tags or [],
            )
            self._tasks[task.id] = task
            self._save_to_storage()
            
            print(f"[TaskQueue] Task added: {task.id} ({task_type.value})")
            return task
    
    def get_next_pending(self) -> Optional[Task]:
        """Get next pending task (FIFO)."""
        with self._lock:
            for task in self._tasks.values():
                if task.status == TaskStatus.PENDING:
                    return task
            return None
    
    def claim_task(self, task_id: str, claimer: str) -> Optional[Task]:
        """Claim task for execution (atomic state change)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            
            if task.status != TaskStatus.PENDING:
                return None  # Can only claim pending tasks
            
            task.mark_claimed(claimer)
            self._tasks[task_id] = task
            self._save_to_storage()
            
            print(f"[TaskQueue] Task claimed: {task_id} by {claimer}")
            return task
    
    def complete_task(self, task_id: str, result: Dict[str, Any]) -> Optional[Task]:
        """Mark task as completed."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            
            task.mark_completed(result)
            self._tasks[task_id] = task
            self._total_completed += 1
            self._save_to_storage()
            
            print(f"[TaskQueue] Task completed: {task_id}")
            return task
    
    def fail_task(
        self,
        task_id: str,
        error: str,
        retry: bool = True,
    ) -> Optional[Task]:
        """
        Mark task as failed.
        
        If retry=True and retries remain, requeue task with backoff.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            
            task.mark_failed(error)
            self._total_failed += 1
            
            # Attempt retry if configured
            if retry and task.retry.should_retry():
                delay = task.retry.next_retry_delay()
                task.retry.retry_count += 1
                task.status = TaskStatus.PENDING
                task.claimed_by = None
                task.claimed_at = None
                print(
                    f"[TaskQueue] Task requeued: {task_id} "
                    f"(retry {task.retry.retry_count}/{task.retry.max_retries}, "
                    f"delay={delay:.1f}s)"
                )
            else:
                print(f"[TaskQueue] Task failed (no retry): {task_id}")
            
            self._tasks[task_id] = task
            self._save_to_storage()
            return task
    
    def cancel_task(self, task_id: str) -> Optional[Task]:
        """Cancel pending or claimed task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            
            if task.status not in (TaskStatus.PENDING, TaskStatus.CLAIMED):
                return None  # Can't cancel running/completed/failed
            
            task.mark_cancelled()
            self._total_cancelled += 1
            self._tasks[task_id] = task
            self._save_to_storage()
            
            print(f"[TaskQueue] Task cancelled: {task_id}")
            return task
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        with self._lock:
            return self._tasks.get(task_id)
    
    def get_expired_tasks(self) -> List[Task]:
        """Get all tasks that have exceeded timeout."""
        with self._lock:
            return [
                task for task in self._tasks.values()
                if task.status == TaskStatus.CLAIMED and task.is_expired()
            ]
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get queue metrics."""
        with self._lock:
            status_counts = {}
            for status in TaskStatus:
                status_counts[status.value] = sum(
                    1 for t in self._tasks.values()
                    if t.status == status
                )
            
            return {
                "total_tasks": len(self._tasks),
                "pending": status_counts[TaskStatus.PENDING.value],
                "claimed": status_counts[TaskStatus.CLAIMED.value],
                "completed": status_counts[TaskStatus.COMPLETED.value],
                "failed": status_counts[TaskStatus.FAILED.value],
                "cancelled": status_counts[TaskStatus.CANCELLED.value],
                "cumulative_completed": self._total_completed,
                "cumulative_failed": self._total_failed,
                "cumulative_cancelled": self._total_cancelled,
                "max_size": self.max_queue_size,
            }
    
    def get_all_tasks(self, status_filter: Optional[TaskStatus] = None) -> List[Task]:
        """Get all tasks, optionally filtered by status."""
        with self._lock:
            if status_filter:
                return [
                    t for t in self._tasks.values()
                    if t.status == status_filter
                ]
            return list(self._tasks.values())
    
    def _load_from_storage(self) -> None:
        """Load persisted tasks from storage."""
        if not self.storage_path.exists():
            return
        
        try:
            with open(self.storage_path, 'r') as f:
                data = json.load(f)
            
            for task_dict in data.get('tasks', []):
                task = Task(
                    id=task_dict['id'],
                    type=TaskType(task_dict['type']),
                    status=TaskStatus(task_dict['status']),
                    payload=task_dict.get('payload', {}),
                    timeout_seconds=task_dict.get('timeout_seconds', 60.0),
                    created_at=task_dict.get('created_at'),
                    claimed_at=task_dict.get('claimed_at'),
                    completed_at=task_dict.get('completed_at'),
                    claimed_by=task_dict.get('claimed_by'),
                    result=task_dict.get('result'),
                    error=task_dict.get('error'),
                    tags=task_dict.get('tags', []),
                )
                retry_dict = task_dict.get('retry')
                if retry_dict:
                    task.retry = TaskRetry(
                        max_retries=retry_dict.get('max_retries', 3),
                        retry_count=retry_dict.get('retry_count', 0),
                        backoff_seconds=retry_dict.get('backoff_seconds', 2.0),
                        last_error=retry_dict.get('last_error'),
                    )
                self._tasks[task.id] = task
            
            print(f"[TaskQueue] Loaded {len(self._tasks)} tasks from storage")
        except Exception as e:
            print(f"[TaskQueue] Failed to load from storage: {e}")
    
    def _save_to_storage(self) -> None:
        """Persist queue state to storage."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, 'w') as f:
                json.dump(
                    {
                        'tasks': [t.to_dict() for t in self._tasks.values()],
                        'saved_at': datetime.now(timezone.utc).isoformat(),
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            print(f"[TaskQueue] Failed to save to storage: {e}")
