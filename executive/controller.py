from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .dispatcher import DispatchEngine
from .identity import FileBackedIdentityProvider, IdentityProvider
from .models import (
    AgentCallback,
    CycleAudit,
    DispatchEnvelope,
    ExecutiveAgenda,
    ExecutiveConfig,
    Goal,
    GoalStatus,
    InstitutionProfile,
    InstitutionProposal,
    Mission,
    MissionStatus,
    ProposalStatus,
    ReflectionRecord,
    Task,
    TaskStatus,
    utc_now,
)
from .monitor import MonitoringEngine
from .persistence import ExecutiveStore
from .planner import PlanningEngine
from .reflector import ReflectionEngine
from .world import WorldModelEngine


def _priority_rank(priority: str) -> int:
    ranks = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    return ranks.get(str(priority or 'medium').strip().lower(), 2)


@dataclass
class CycleOutcome:
    observed_goals: int
    planned_goals: int
    dispatched_tasks: int
    reflected_goals: int
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'observed_goals': self.observed_goals,
            'planned_goals': self.planned_goals,
            'dispatched_tasks': self.dispatched_tasks,
            'reflected_goals': self.reflected_goals,
            'notes': list(self.notes),
        }


class ExecutiveController:
    def __init__(
        self,
        *,
        store: ExecutiveStore | None = None,
        identity_provider: IdentityProvider | None = None,
        config: ExecutiveConfig | None = None,
    ) -> None:
        self.config = config or ExecutiveConfig()
        self.store = store or ExecutiveStore(self.config.store_path)
        identity_path = f"storage/executive/identity_{self.config.identity_profile}.json"
        self.identity = identity_provider or FileBackedIdentityProvider(path=identity_path)
        self.planner = PlanningEngine(default_agent='general')
        self.dispatcher = DispatchEngine()
        self.monitor = MonitoringEngine()
        self.reflector = ReflectionEngine()
        self.world = WorldModelEngine(self.store)
        if self.store.get_config() is None:
            self.store.set_config(self.config)
        self.world.bootstrap_valhalla()
        self._bootstrap_institution_profiles()

    def _bootstrap_institution_profiles(self) -> None:
        defaults = {
            'workshop': InstitutionProfile(
                institution_id='workshop',
                authority_level=3,
                proposal_types=['world_mutation'],
                resource_limits={'max_quantity_delta': 500.0},
                review_requirements=['mission_control_review'],
                escalation_rules=['escalate_to_sentinel_on_violation'],
            ),
            'academy': InstitutionProfile(
                institution_id='academy',
                authority_level=2,
                proposal_types=['research_note'],
                resource_limits={'max_quantity_delta': 25.0},
                review_requirements=['mission_control_review'],
                escalation_rules=['escalate_to_mission_control'],
            ),
            'laboratory': InstitutionProfile(
                institution_id='laboratory',
                authority_level=2,
                proposal_types=['world_mutation', 'experiment'],
                resource_limits={'max_quantity_delta': 100.0},
                review_requirements=['mission_control_review'],
                escalation_rules=['escalate_to_sentinel_on_violation'],
            ),
            'mission_control': InstitutionProfile(
                institution_id='mission_control',
                authority_level=4,
                proposal_types=['world_mutation', 'policy_update'],
                resource_limits={'max_quantity_delta': 500.0},
                review_requirements=['constitutional_check'],
                escalation_rules=['escalate_to_sentinel_on_violation'],
            ),
            'memory_vault': InstitutionProfile(
                institution_id='memory_vault',
                authority_level=2,
                proposal_types=['archive_update', 'knowledge_update'],
                resource_limits={'max_quantity_delta': 50.0},
                review_requirements=['mission_control_review'],
                escalation_rules=['escalate_to_mission_control'],
            ),
            'sentinel': InstitutionProfile(
                institution_id='sentinel',
                authority_level=5,
                proposal_types=['world_mutation', 'policy_update', 'safety_intervention'],
                resource_limits={'max_quantity_delta': 1000.0},
                review_requirements=['constitutional_check'],
                escalation_rules=['final_authority'],
                can_veto=True,
            ),
        }
        for institution_id, profile in defaults.items():
            if self.store.get_institution_profile(institution_id) is None:
                self.store.upsert_institution_profile(profile)

    def identity_snapshot(self) -> Dict[str, Any]:
        return self.identity.snapshot()

    def world_snapshot(self) -> Dict[str, Any]:
        return {
            'civilizations': len(self.store.list_civilizations()),
            'institutions': len(self.store.list_institutions()),
            'institution_profiles': len(self.store.list_institution_profiles()),
            'resources': len(self.store.list_resources()),
            'knowledge_assets': len(self.store.list_knowledge_assets()),
            'treaties': len(self.store.list_treaties()),
            'world_mutations': len(self.store.list_world_mutations()),
            'proposals': len(self.store.list_proposals()),
        }

    def _get_institution_profile(self, institution_id: str) -> InstitutionProfile:
        profile = self.store.get_institution_profile(institution_id)
        if profile is None:
            raise PermissionError(f'missing_institution_profile:{institution_id}')
        return profile

    def _validate_proposal_type(self, profile: InstitutionProfile, proposal_type: str) -> None:
        allowed = set(profile.proposal_types)
        if proposal_type not in allowed:
            raise PermissionError(f'proposal_type_not_allowed:{profile.institution_id}:{proposal_type}')

    def _enforce_resource_limits(self, profile: InstitutionProfile, proposal_type: str, payload: Dict[str, Any]) -> None:
        if proposal_type != 'world_mutation':
            return

        mutation_type = str(payload.get('mutation_type', '')).strip()
        target_entity = str(payload.get('target_entity', '')).strip()
        mutation_payload = dict(payload.get('payload') or {})
        if mutation_type != 'resource.update_quantity' or not target_entity:
            return

        resource = self.store.get_resource(target_entity)
        if resource is None or 'quantity' not in mutation_payload:
            return

        requested_quantity = float(mutation_payload['quantity'])
        current_quantity = float(resource.quantity)
        delta = abs(requested_quantity - current_quantity)

        max_delta = profile.resource_limits.get('max_quantity_delta')
        if max_delta is not None and delta > float(max_delta):
            raise PermissionError(
                f'resource_limit_exceeded:{profile.institution_id}:max_quantity_delta:{float(max_delta)}'
            )

    def submit_proposal(self, institution_id: str, proposal_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        institution = next((item for item in self.store.list_institutions() if item.id == institution_id), None)
        if institution is None:
            raise ValueError(f'unknown institution: {institution_id}')

        profile = self._get_institution_profile(institution_id)
        self._validate_proposal_type(profile, proposal_type)
        self._enforce_resource_limits(profile, proposal_type, payload)

        proposal = InstitutionProposal(
            proposal_id=f'proposal_{uuid4().hex}',
            institution_id=institution_id,
            proposal_type=proposal_type,
            payload=dict(payload or {}),
            outcome={
                'profile_authority_level': profile.authority_level,
                'review_requirements': list(profile.review_requirements),
                'escalation_rules': list(profile.escalation_rules),
            },
        )
        self.store.upsert_proposal(proposal)
        self._refresh_executive_agenda()
        return proposal.to_dict()

    def review_proposal(self, proposal_id: str, approve: bool, rationale: str = '') -> Dict[str, Any]:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f'unknown proposal: {proposal_id}')
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError('proposal_not_pending')

        allowed, reason = self.identity.check_action(
            action=f'proposal_review:{proposal.proposal_type}',
            context={'proposal_id': proposal.proposal_id, 'institution_id': proposal.institution_id},
        )
        if not allowed:
            raise PermissionError(reason)

        profile = self._get_institution_profile(proposal.institution_id)
        proposal.status = ProposalStatus.APPROVED if approve else ProposalStatus.REJECTED
        proposal.rationale = rationale
        proposal.reviewed_at = utc_now()
        proposal.outcome = {
            **proposal.outcome,
            'identity_result': reason,
            'approved': approve,
            'review_requirements': list(profile.review_requirements),
            'escalation_rules': list(profile.escalation_rules),
        }
        self.store.upsert_proposal(proposal)
        self._refresh_executive_agenda()
        return proposal.to_dict()

    def veto_proposal(self, proposal_id: str, rationale: str = '', veto_institution_id: str = 'sentinel') -> Dict[str, Any]:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f'unknown proposal: {proposal_id}')
        if proposal.status not in {ProposalStatus.PENDING, ProposalStatus.APPROVED}:
            raise ValueError('proposal_not_vetoable')

        profile = self._get_institution_profile(veto_institution_id)
        if not profile.can_veto:
            raise PermissionError(f'veto_not_allowed:{veto_institution_id}')

        proposal.status = ProposalStatus.REJECTED
        proposal.rationale = rationale or f'vetoed_by_{veto_institution_id}'
        proposal.reviewed_at = utc_now()
        proposal.outcome = {
            **proposal.outcome,
            'vetoed': True,
            'vetoed_by': veto_institution_id,
            'veto_reason': proposal.rationale,
            'escalation_rules': list(profile.escalation_rules),
        }
        self.store.upsert_proposal(proposal)
        self._refresh_executive_agenda()
        return proposal.to_dict()

    def execute_proposal(self, proposal_id: str, actor: str = 'executive') -> Dict[str, Any]:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f'unknown proposal: {proposal_id}')
        if proposal.status != ProposalStatus.APPROVED:
            raise ValueError('proposal_not_approved')

        if proposal.proposal_type != 'world_mutation':
            raise ValueError('unsupported_proposal_type')

        mutation_type = str(proposal.payload.get('mutation_type', '')).strip()
        target_entity = str(proposal.payload.get('target_entity', '')).strip()
        payload = dict(proposal.payload.get('payload') or {})
        if not mutation_type or not target_entity:
            raise ValueError('invalid_world_mutation_proposal_payload')

        allowed, reason = self.identity.check_action(
            action=f'world_mutation:{mutation_type}',
            context={
                'actor': actor,
                'institution': proposal.institution_id,
                'proposal_id': proposal.proposal_id,
                'target_entity': target_entity,
            },
        )
        if not allowed:
            raise PermissionError(reason)

        mutation = self.world.record_mutation(
            actor=actor,
            institution=proposal.institution_id,
            proposal_id=proposal.proposal_id,
            mutation_type=mutation_type,
            target_entity=target_entity,
            payload=payload,
            identity_result=reason,
        )
        proposal.status = ProposalStatus.EXECUTED
        proposal.executed_at = utc_now()
        proposal.outcome = {**proposal.outcome, 'mutation_id': mutation.mutation_id}
        self.store.upsert_proposal(proposal)
        self._refresh_executive_agenda()
        return mutation.to_dict()

    def apply_world_mutation(
        self,
        *,
        actor: str,
        institution: str,
        proposal_id: str,
        mutation_type: str,
        target_entity: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise PermissionError('direct_world_mutation_disabled_use_proposal_pipeline')


    def _build_executive_agenda(self) -> ExecutiveAgenda:
        goals = self.store.list_goals()
        proposals = self.store.list_proposals()

        active_goals = [
            goal.goal_id
            for goal in goals
            if goal.status in {GoalStatus.DRAFT, GoalStatus.ACTIVE, GoalStatus.BLOCKED}
        ]
        pending_proposals = [
            proposal.proposal_id
            for proposal in proposals
            if proposal.status == ProposalStatus.PENDING
        ]
        institution_requests = sorted(
            {
                proposal.institution_id
                for proposal in proposals
                if proposal.status == ProposalStatus.PENDING
            }
        )
        strategic_priorities = [
            goal.goal_id
            for goal in sorted(
                [item for item in goals if item.status in {GoalStatus.DRAFT, GoalStatus.ACTIVE}],
                key=lambda item: (_priority_rank(item.priority), item.created_at),
            )
        ]

        blocked_items = [
            f'goal:{goal.goal_id}'
            for goal in goals
            if goal.status == GoalStatus.BLOCKED
        ]
        blocked_items.extend(
            [
                f'proposal:{proposal.proposal_id}'
                for proposal in proposals
                if proposal.status == ProposalStatus.REJECTED
            ]
        )

        return ExecutiveAgenda(
            active_goals=active_goals,
            pending_proposals=pending_proposals,
            institution_requests=institution_requests,
            strategic_priorities=strategic_priorities,
            blocked_items=blocked_items,
            updated_at=utc_now(),
        )

    def _refresh_executive_agenda(self) -> ExecutiveAgenda:
        agenda = self._build_executive_agenda()
        self.store.set_executive_agenda(agenda)
        return agenda

    def _sync_identity_dynamic(self) -> None:
        if not hasattr(self.identity, 'update_dynamic'):
            return
        active_missions = [
            mission_id
            for mission_id, mission in self.store._state.get('missions', {}).items()
            if str(mission.get('status', 'active')).lower() in {'draft', 'active', 'paused'}
        ]
        active_goals = [
            goal.goal_id
            for goal in self.store.list_goals()
            if goal.status in {GoalStatus.DRAFT, GoalStatus.ACTIVE, GoalStatus.BLOCKED}
        ]
        focus = [goal.title for goal in self.store.list_goals() if goal.status in {GoalStatus.DRAFT, GoalStatus.ACTIVE}][:5]
        self.identity.update_dynamic(
            current_focus=focus,
            active_missions=active_missions,
            active_goals=active_goals,
        )

    def create_mission(
        self,
        title: str,
        objectives: Optional[List[str]] = None,
        target_date: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Mission:
        mission = Mission(
            mission_id=f'mission_{uuid4().hex}',
            title=title,
            objectives=list(objectives or []),
            target_date=target_date,
            metadata=dict(metadata or {}),
        )
        mission = self.store.upsert_mission(mission)
        self._sync_identity_dynamic()
        return mission

    def create_goal(
        self,
        title: str,
        *,
        mission_id: str,
        priority: Optional[str] = None,
        success_criteria: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Goal:
        if not self.store.get_mission(mission_id):
            raise ValueError(f'unknown mission: {mission_id}')
        goal = Goal(
            goal_id=f'goal_{uuid4().hex}',
            mission_id=mission_id,
            title=title,
            priority=priority or self.config.default_priority,
            success_criteria=list(success_criteria or []),
            metadata=dict(metadata or {}),
        )
        goal = self.store.upsert_goal(goal)
        mission = self.store.get_mission(mission_id)
        if mission and goal.goal_id not in mission.goals:
            mission.goals.append(goal.goal_id)
            mission.updated_at = utc_now()
            self.store.upsert_mission(mission)
        self._sync_identity_dynamic()
        return goal

    def load_goal(self, goal_id: str) -> Optional[Goal]:
        return self.store.get_goal(goal_id)

    def archive_goal(self, goal_id: str) -> Goal:
        goal = self.store.get_goal(goal_id)
        if goal is None:
            raise ValueError(f'unknown goal: {goal_id}')
        goal.status = GoalStatus.ARCHIVED
        goal.updated_at = utc_now()
        goal = self.store.upsert_goal(goal)
        self._sync_identity_dynamic()
        return goal

    def _mission_for_goal(self, goal: Goal) -> Mission:
        mission = self.store.get_mission(goal.mission_id)
        if mission is None:
            raise ValueError(f'missing mission for goal: {goal.goal_id}')
        return mission

    def generate_plan(self, goal_id: str) -> List[Task]:
        goal = self.load_goal(goal_id)
        if goal is None:
            raise ValueError(f'unknown goal: {goal_id}')
        mission = self._mission_for_goal(goal)
        tasks = self.planner.generate_plan(mission, goal, self.identity)
        for task in tasks:
            self.store.upsert_task(task)
            if task.task_id not in goal.task_ids:
                goal.task_ids.append(task.task_id)
        goal.updated_at = utc_now()
        self.store.upsert_goal(goal)
        return tasks

    def dispatch_task(self, task_id: str, agent_name: Optional[str] = None) -> DispatchEnvelope:
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f'unknown task: {task_id}')
        if task.status not in {TaskStatus.PENDING, TaskStatus.ASSIGNED}:
            raise ValueError(f'task not dispatchable: {task.status.value}')
        resolved_agent = agent_name or task.agent or self.config.planner_agent
        envelope = self.dispatcher.dispatch_task(task, resolved_agent, self.identity)
        task.agent = resolved_agent
        task.status = TaskStatus.ASSIGNED
        task.assigned_at = utc_now()
        task.updated_at = utc_now()
        self.store.upsert_task(task)
        self.store.record_dispatch(envelope)
        if self.config.simulate_execution:
            self._simulate_callback(task, envelope)
        return envelope

    def _simulate_callback(self, task: Task, envelope: DispatchEnvelope) -> AgentCallback:
        callback = AgentCallback(
            callback_id=f'callback_{uuid4().hex}',
            task_id=task.task_id,
            agent_name=envelope.agent_name,
            status='completed',
            payload={'result': f'Simulated completion for {task.title}', 'task_id': task.task_id},
            metadata={'simulated': True},
        )
        self.receive_callback(callback)
        return callback

    def receive_callback(self, callback: AgentCallback) -> AgentCallback:
        task = self.store.get_task(callback.task_id)
        if task is None:
            raise ValueError(f'unknown task: {callback.task_id}')
        self.store.record_callback(callback)
        task.outputs = dict(callback.payload)
        task.updated_at = utc_now()
        if callback.status == 'completed':
            task.status = TaskStatus.COMPLETED
            task.completed_at = utc_now()
        elif callback.status == 'failed':
            task.status = TaskStatus.FAILED
            task.completed_at = utc_now()
        else:
            task.status = TaskStatus.WAITING
        self.store.upsert_task(task)
        goal = self.store.get_goal(task.goal_id)
        if goal:
            goal.updated_at = utc_now()
            self.store.upsert_goal(goal)
        return callback

    def reflect(self, goal_id: str) -> ReflectionRecord:
        goal = self.load_goal(goal_id)
        if goal is None:
            raise ValueError(f'unknown goal: {goal_id}')
        mission = self._mission_for_goal(goal)
        tasks = self.store.list_tasks(goal_id=goal_id)
        reflection = self.reflector.reflect(mission, goal, tasks)
        self.store.upsert_reflection(reflection)
        goal.reflection_ids.append(reflection.reflection_id)
        goal.updated_at = utc_now()
        if all(task.status == TaskStatus.COMPLETED for task in tasks) and tasks:
            goal.status = GoalStatus.COMPLETED
            if self.config.auto_archive_completed_goals:
                goal.status = GoalStatus.ARCHIVED
        self.store.upsert_goal(goal)
        mission_obj = self.store.get_mission(goal.mission_id)
        if mission_obj and all(
            self.store.get_goal(goal_id_item).status in {GoalStatus.COMPLETED, GoalStatus.ARCHIVED}
            for goal_id_item in mission_obj.goals
            if self.store.get_goal(goal_id_item)
        ):
            mission_obj.status = MissionStatus.COMPLETED
            self.store.upsert_mission(mission_obj)
        self._sync_identity_dynamic()
        return reflection

    def run_cycle(self) -> Dict[str, Any]:
        started = time.monotonic()
        notes: List[str] = []
        self._refresh_executive_agenda()

        goals = [goal for goal in self.store.list_goals() if goal.status in {GoalStatus.DRAFT, GoalStatus.ACTIVE}]
        observed = len(goals)
        if not goals:
            outcome = CycleOutcome(0, 0, 0, 0, ['no_active_goals'])
            self.store.append_cycle_log({'timestamp': utc_now(), **outcome.to_dict()})
            self.store.append_cycle_audit(
                CycleAudit(
                    cycle_id=f'cycle_{uuid4().hex}',
                    timestamp=utc_now(),
                    proposals_generated=len(self.store.list_proposals()),
                    proposals_approved=0,
                    proposals_rejected=0,
                    resources_consumed=0.0,
                    rollback_triggered=False,
                    metadata={'reason': 'no_active_goals'},
                )
            )
            self._sync_identity_dynamic()
            return outcome.to_dict()

        prioritized = sorted(goals, key=lambda goal: (_priority_rank(goal.priority), goal.created_at))
        planned = 0
        dispatched = 0
        reflected = 0

        for idx, goal in enumerate(prioritized):
            if idx >= self.config.max_active_goals or idx >= self.config.max_cycles_per_run:
                notes.append('budget:max_cycles_per_run')
                break
            runtime_minutes = (time.monotonic() - started) / 60.0
            if runtime_minutes > self.config.max_runtime_minutes:
                notes.append('budget:max_runtime_minutes')
                break

            if not self.store.list_tasks(goal_id=goal.goal_id):
                self.generate_plan(goal.goal_id)
                planned += 1
                notes.append(f'planned:{goal.goal_id}')

            tasks = self.store.list_tasks(goal_id=goal.goal_id)
            for task in tasks:
                if dispatched >= self.config.max_dispatches:
                    notes.append('budget:max_dispatches')
                    break
                if task.status == TaskStatus.PENDING:
                    self.dispatch_task(task.task_id, agent_name=task.agent or self.config.planner_agent)
                    dispatched += 1
            if dispatched >= self.config.max_dispatches:
                break

            tasks = self.store.list_tasks(goal_id=goal.goal_id)
            summary = self.monitor.summarize(goal, tasks)
            notes.append(f"monitor:{goal.goal_id}:{summary['completed']}/{len(tasks)}")

            if summary['all_complete']:
                self.reflect(goal.goal_id)
                reflected += 1
                notes.append(f'reflect:{goal.goal_id}')
                if self.config.auto_archive_completed_goals:
                    self.archive_goal(goal.goal_id)
                    notes.append(f'archive:{goal.goal_id}')

        outcome = CycleOutcome(observed, planned, dispatched, reflected, notes)
        self.store.append_cycle_log({'timestamp': utc_now(), **outcome.to_dict()})

        proposals = self.store.list_proposals()
        approved = len([p for p in proposals if p.status in {ProposalStatus.APPROVED, ProposalStatus.EXECUTED}])
        rejected = len([p for p in proposals if p.status == ProposalStatus.REJECTED])
        resource_cost = float(dispatched)
        rollback = resource_cost > self.config.max_resource_cost
        self.store.append_cycle_audit(
            CycleAudit(
                cycle_id=f'cycle_{uuid4().hex}',
                timestamp=utc_now(),
                proposals_generated=len(proposals),
                proposals_approved=approved,
                proposals_rejected=rejected,
                resources_consumed=resource_cost,
                rollback_triggered=rollback,
                metadata={
                    'max_cycles_per_run': self.config.max_cycles_per_run,
                    'max_runtime_minutes': self.config.max_runtime_minutes,
                    'max_dispatches': self.config.max_dispatches,
                    'max_resource_cost': self.config.max_resource_cost,
                },
            )
        )

        self._refresh_executive_agenda()
        self._sync_identity_dynamic()
        return outcome.to_dict()
