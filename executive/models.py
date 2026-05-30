from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MissionStatus(str, Enum):
    DRAFT = 'draft'
    ACTIVE = 'active'
    PAUSED = 'paused'
    COMPLETED = 'completed'
    ARCHIVED = 'archived'


class GoalStatus(str, Enum):
    DRAFT = 'draft'
    ACTIVE = 'active'
    BLOCKED = 'blocked'
    COMPLETED = 'completed'
    ARCHIVED = 'archived'


class TaskStatus(str, Enum):
    PENDING = 'pending'
    ASSIGNED = 'assigned'
    RUNNING = 'running'
    WAITING = 'waiting'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class ProposalStatus(str, Enum):
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    EXECUTED = 'executed'


@dataclass
class WorldMutation:
    mutation_id: str
    actor: str
    institution: str
    proposal_id: str
    mutation_type: str
    target_entity: str
    payload: Dict[str, Any]
    identity_result: str
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WorldMutation':
        return cls(**dict(data))


@dataclass
class Civilization:
    id: str
    name: str
    mission: str
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Civilization':
        return cls(**dict(data))


@dataclass
class Institution:
    id: str
    civilization_id: str
    role: str
    mandate: str
    authority_level: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Institution':
        return cls(**dict(data))


@dataclass
class InstitutionProfile:
    institution_id: str
    authority_level: int
    proposal_types: List[str] = field(default_factory=list)
    resource_limits: Dict[str, float] = field(default_factory=dict)
    review_requirements: List[str] = field(default_factory=list)
    escalation_rules: List[str] = field(default_factory=list)
    can_veto: bool = False
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InstitutionProfile':
        return cls(**dict(data))




@dataclass
class Resource:
    id: str
    type: str
    quantity: float
    owner: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Resource':
        return cls(**dict(data))


@dataclass
class KnowledgeAsset:
    id: str
    title: str
    classification: str
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'KnowledgeAsset':
        return cls(**dict(data))


@dataclass
class Treaty:
    id: str
    participants: List[str] = field(default_factory=list)
    terms: List[str] = field(default_factory=list)
    status: str = 'active'

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Treaty':
        return cls(**dict(data))


@dataclass
class InstitutionProposal:
    proposal_id: str
    institution_id: str
    proposal_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    status: ProposalStatus = ProposalStatus.PENDING
    rationale: str = ''
    outcome: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    reviewed_at: Optional[str] = None
    executed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['status'] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InstitutionProposal':
        payload = dict(data)
        payload['status'] = ProposalStatus(payload.get('status', ProposalStatus.PENDING.value))
        return cls(**payload)


@dataclass
class Mission:
    mission_id: str
    title: str
    objectives: List[str] = field(default_factory=list)
    goals: List[str] = field(default_factory=list)
    status: MissionStatus = MissionStatus.ACTIVE
    start_date: str = field(default_factory=utc_now)
    target_date: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['status'] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Mission':
        payload = dict(data)
        payload['status'] = MissionStatus(payload.get('status', MissionStatus.ACTIVE.value))
        return cls(**payload)


@dataclass
class Goal:
    goal_id: str
    mission_id: str
    title: str
    priority: str = 'medium'
    status: GoalStatus = GoalStatus.ACTIVE
    owner: str = 'ANDIE'
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    success_criteria: List[str] = field(default_factory=list)
    subtasks: List[str] = field(default_factory=list)
    task_ids: List[str] = field(default_factory=list)
    reflection_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['status'] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Goal':
        payload = dict(data)
        payload['status'] = GoalStatus(payload.get('status', GoalStatus.ACTIVE.value))
        return cls(**payload)


@dataclass
class Task:
    task_id: str
    goal_id: str
    title: str
    description: str = ''
    priority: str = 'medium'
    status: TaskStatus = TaskStatus.PENDING
    agent: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    assigned_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['status'] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Task':
        payload = dict(data)
        payload['status'] = TaskStatus(payload.get('status', TaskStatus.PENDING.value))
        return cls(**payload)


@dataclass
class ReflectionRecord:
    reflection_id: str
    mission_id: str
    goal_id: str
    title: str
    success: bool
    lessons: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    improvements: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ReflectionRecord':
        return cls(**dict(data))


@dataclass
class CycleBudget:
    max_cycles: int = 10
    max_runtime_minutes: int = 15
    max_dispatches: int = 50
    max_resource_cost: float = 1000.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CycleAudit:
    cycle_id: str
    timestamp: str
    proposals_generated: int
    proposals_approved: int
    proposals_rejected: int
    resources_consumed: float
    rollback_triggered: bool
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CycleAudit':
        return cls(**dict(data))


@dataclass
class ExecutiveConfig:
    store_path: str = 'storage/executive/executive_state.json'
    heartbeat_interval_seconds: float = 2.0
    default_priority: str = 'medium'
    simulate_execution: bool = True
    auto_archive_completed_goals: bool = False
    max_active_goals: int = 10
    planner_agent: str = 'planner'
    monitor_agent: str = 'monitor'
    identity_profile: str = 'constitution'
    max_cycles_per_run: int = 10
    max_runtime_minutes: int = 15
    max_dispatches: int = 50
    max_resource_cost: float = 1000.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExecutiveConfig':
        return cls(**dict(data))


@dataclass
class DispatchEnvelope:
    envelope_id: str
    task_id: str
    agent_name: str
    callback_channel: str
    payload: Dict[str, Any]
    created_at: str = field(default_factory=utc_now)
    status: str = 'pending'
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DispatchEnvelope':
        return cls(**dict(data))


@dataclass
class AgentCallback:
    callback_id: str
    task_id: str
    agent_name: str
    status: str
    payload: Dict[str, Any] = field(default_factory=dict)
    received_at: str = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentCallback':
        return cls(**dict(data))
