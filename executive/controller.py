from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4
import os

from .agenda_policy import load_agenda_policy, normalize_agenda_policy
from .operational_slo import load_operational_slos
from .dispatcher import DispatchEngine
from .identity import FileBackedIdentityProvider, IdentityProvider
from .models import (
    AgendaDecision,
    AgentCallback,
    CycleAudit,
    DispatchEnvelope,
    ExecutiveAgenda,
    ExecutiveConfig,
    Goal,
    GoalStatus,
    InstitutionProfile,
    InstitutionProposal,
    Intent,
    IntentStatus,
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
        self._agenda_policy_path = os.environ.get('ANDIE_AGENDA_POLICY_PATH', 'storage/executive/agenda_policy.json')
        self._agenda_policy = load_agenda_policy(self._agenda_policy_path)
        self._operational_slo_path = os.environ.get(
            'ANDIE_EXECUTIVE_SLO_PATH',
            'storage/executive/operational_slos.json',
        )
        self._operational_slos = load_operational_slos(self._operational_slo_path)
        self._operational_metric_window = max(20, int(os.environ.get('ANDIE_EXECUTIVE_METRIC_WINDOW', '200')))
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

        metrics = self._load_operational_metrics()
        try:
            profile = self._get_institution_profile(institution_id)
            self._validate_proposal_type(profile, proposal_type)
            self._enforce_resource_limits(profile, proposal_type, payload)
        except PermissionError:
            self._record_policy_violation(metrics)
            self._save_operational_metrics(metrics)
            raise

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

        metrics = self._load_operational_metrics()
        allowed, reason = self.identity.check_action(
            action=f'proposal_review:{proposal.proposal_type}',
            context={'proposal_id': proposal.proposal_id, 'institution_id': proposal.institution_id},
        )
        if not allowed:
            self._record_identity_bypass_attempt(metrics)
            self._record_policy_violation(metrics)
            self._save_operational_metrics(metrics)
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

        metrics = self._load_operational_metrics()
        profile = self._get_institution_profile(veto_institution_id)
        if not profile.can_veto:
            self._record_policy_violation(metrics)
            self._save_operational_metrics(metrics)
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

        metrics = self._load_operational_metrics()
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
            self._record_identity_bypass_attempt(metrics)
            self._record_policy_violation(metrics)
            self._save_operational_metrics(metrics)
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

        mission_refs = [
            {
                'mission_id': str(item.get('mission_id', mission_id)),
                'title': str(item.get('title', '')),
                'status': str(item.get('status', 'active')).lower(),
            }
            for mission_id, item in self.store._state.get('missions', {}).items()
            if str(item.get('status', 'active')).lower() in {'draft', 'active', 'paused'}
        ]

        goal_refs = [
            {
                'goal_id': goal.goal_id,
                'mission_id': goal.mission_id,
                'title': goal.title,
                'status': goal.status.value,
                'priority': goal.priority,
            }
            for goal in goals
            if goal.status in {GoalStatus.DRAFT, GoalStatus.ACTIVE, GoalStatus.BLOCKED}
        ]

        blocker_refs = [
            {'id': item, 'status': 'blocked'}
            for item in blocked_items
        ]

        proposal_refs = [
            {
                'proposal_id': proposal.proposal_id,
                'institution_id': proposal.institution_id,
                'proposal_type': proposal.proposal_type,
                'status': proposal.status.value,
            }
            for proposal in proposals
            if proposal.status == ProposalStatus.PENDING
        ]

        institution_request_refs = [
            {'institution_id': inst, 'request_type': 'pending_proposal'}
            for inst in institution_requests
        ]

        priority_refs = [
            {'priority_id': goal_id, 'source': 'goal', 'score': 0, 'status': 'ready'}
            for goal_id in strategic_priorities
        ]

        return ExecutiveAgenda(
            active_goals=active_goals,
            pending_proposals=pending_proposals,
            institution_requests=institution_requests,
            strategic_priorities=strategic_priorities,
            blocked_items=blocked_items,
            missions=mission_refs,
            goals=goal_refs,
            priorities=priority_refs,
            blockers=blocker_refs,
            pending_proposal_refs=proposal_refs,
            institution_request_refs=institution_request_refs,
            budget_status={
                'max_active_goals': self.config.max_active_goals,
                'max_dispatches': self.config.max_dispatches,
                'max_resource_cost': self.config.max_resource_cost,
                'max_runtime_minutes': self.config.max_runtime_minutes,
            },
            updated_at=utc_now(),
        )

    def _refresh_executive_agenda(self) -> ExecutiveAgenda:
        agenda = self._build_executive_agenda()
        self.store.set_executive_agenda(agenda)
        return agenda

    def _score_agenda_signal(self, signal: Dict[str, Any]) -> int:
        institution_id = str(signal.get('institution_id', '')).strip().lower()
        signal_type = str(signal.get('type', '')).strip().lower()

        if institution_id == 'sentinel' and signal_type in {'alert', 'security_alert'}:
            return 100
        if signal_type == 'mission_blocker' or bool(signal.get('is_blocker')):
            return 80
        if institution_id == 'workshop' and signal_type in {'proposal', 'tool_proposal'}:
            return 50
        if institution_id == 'academy' and signal_type in {'research', 'research_result'}:
            return 40
        return 10

    def _escalation_adjustment(
        self,
        signal: Dict[str, Any],
        *,
        policy: Dict[str, Any],
        base_score: int,
        deferred_count: int,
        repeat_count: int,
    ) -> int:
        institution_id = str(signal.get('institution_id', '')).strip().lower()
        signal_type = str(signal.get('type', '')).strip().lower()
        max_deferred_cycles = int(policy.get('max_deferred_cycles', 3))
        sentinel_escalation_rate = float(policy.get('sentinel_escalation_rate', 1.0))
        academy_decay_rate = float(policy.get('academy_decay_rate', 1.0))
        blocker_escalation_threshold = int(policy.get('blocker_escalation_threshold', 3))

        adjustment = 0

        # Deferred work eventually becomes urgent to prevent starvation.
        if deferred_count >= max_deferred_cycles:
            adjustment += 15

        # Repeated high-risk sentinel alerts must climb in urgency.
        if institution_id == 'sentinel' and signal_type in {'alert', 'security_alert'}:
            sentinel_bonus = max(0.0, float(repeat_count - 1) * 5.0 * sentinel_escalation_rate)
            adjustment += int(round(min(sentinel_bonus, 40.0)))

        # Repeated mission blockers should escalate quickly.
        if signal_type == 'mission_blocker' or bool(signal.get('is_blocker')):
            blocker_steps = max(0, repeat_count - blocker_escalation_threshold + 1)
            adjustment += int(round(min(float(blocker_steps) * 4.0, 16.0)))

        # Academy priority behavior is policy-defined (growth or decay).
        if institution_id == 'academy' and signal_type in {'research', 'research_result'}:
            multiplier = academy_decay_rate ** max(0, repeat_count - 1)
            adjusted_score = int(round(float(base_score) * float(multiplier)))
            adjustment += adjusted_score - int(base_score)

        return adjustment

    def get_agenda_policy(self) -> Dict[str, Any]:
        return dict(self._agenda_policy)

    def _resolve_agenda_policy(self, override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        merged = dict(self._agenda_policy)
        if isinstance(override, dict):
            merged.update(override)
        return normalize_agenda_policy(merged)

    def _default_operational_metrics(self) -> Dict[str, Any]:
        return {
            'window_size': int(self._operational_metric_window),
            'decision_latency_ms': [],
            'agenda_rebuild_ms': [],
            'simulation_latency_ms': [],
            'decision_count': 0,
            'simulation_runs': 0,
            'policy_violations': 0,
            'simulation_state_mutations': 0,
            'identity_bypass_attempts': 0,
            'intent_creation_attempts': 0,
            'intent_creation_successes': 0,
            'intent_completion_count': 0,
            'intent_completion_hours': [],
            'current_cycle': 0,
            'stale_intents': {
                'threshold_cycles': int(self._operational_slos['intent']['stale_intents']['threshold_cycles']),
                'count': 0,
                'intent_ids': [],
            },
            'updated_at': utc_now(),
        }

    def _load_operational_metrics(self) -> Dict[str, Any]:
        metrics = self.store.get_operational_metrics()
        baseline = self._default_operational_metrics()
        if not isinstance(metrics, dict):
            return baseline
        baseline.update({key: metrics.get(key, baseline[key]) for key in baseline})
        baseline['window_size'] = int(self._operational_metric_window)
        stale = dict(baseline.get('stale_intents') or {})
        stale['threshold_cycles'] = int(self._operational_slos['intent']['stale_intents']['threshold_cycles'])
        stale['count'] = int(stale.get('count', 0))
        stale['intent_ids'] = [str(item) for item in stale.get('intent_ids', [])]
        baseline['stale_intents'] = stale
        return baseline

    def _save_operational_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(metrics or {})
        payload['updated_at'] = utc_now()
        return self.store.set_operational_metrics(payload)

    def _append_metric_value(self, metrics: Dict[str, Any], key: str, value: float) -> None:
        window_size = int(self._operational_metric_window)
        current = list(metrics.get(key) or [])
        current.append(round(float(value), 3))
        metrics[key] = current[-window_size:]

    def _record_policy_violation(self, metrics: Dict[str, Any]) -> None:
        metrics['policy_violations'] = int(metrics.get('policy_violations', 0)) + 1

    def _record_identity_bypass_attempt(self, metrics: Dict[str, Any]) -> None:
        metrics['identity_bypass_attempts'] = int(metrics.get('identity_bypass_attempts', 0)) + 1

    def _advance_intent_health(self, metrics: Dict[str, Any]) -> None:
        threshold = int(self._operational_slos['intent']['stale_intents']['threshold_cycles'])
        current_cycle = int(metrics.get('current_cycle', 0))
        stale_ids: List[str] = []

        for intent in self.store.list_intents():
            if intent.status in {IntentStatus.COMPLETED, IntentStatus.FAILED, IntentStatus.CANCELLED}:
                continue
            intent_meta = dict(intent.metadata or {})
            created_cycle = int(intent_meta.get('created_cycle', 0))
            age_cycles = max(0, current_cycle - created_cycle)
            if age_cycles >= threshold:
                stale_ids.append(intent.intent_id)

        metrics['stale_intents'] = {
            'threshold_cycles': threshold,
            'count': len(stale_ids),
            'intent_ids': stale_ids,
        }

    def _p95(self, values: List[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(float(item) for item in values)
        index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
        return round(float(ordered[index]), 3)

    def get_operational_slos(self) -> Dict[str, Any]:
        return dict(self._operational_slos)

    def get_operational_slo_snapshot(self) -> Dict[str, Any]:
        metrics = self._load_operational_metrics()
        targets = self.get_operational_slos()

        decision_p95_ms = self._p95([float(item) for item in metrics.get('decision_latency_ms', [])])
        simulation_p95_ms = self._p95([float(item) for item in metrics.get('simulation_latency_ms', [])])
        rebuild_p95_ms = self._p95([float(item) for item in metrics.get('agenda_rebuild_ms', [])])
        completion_p95_hours = self._p95([float(item) for item in metrics.get('intent_completion_hours', [])])

        creation_attempts = int(metrics.get('intent_creation_attempts', 0))
        creation_successes = int(metrics.get('intent_creation_successes', 0))
        creation_success_rate = (
            round((float(creation_successes) / float(creation_attempts)) * 100.0, 3)
            if creation_attempts > 0
            else 100.0
        )

        decision_count = max(1, int(metrics.get('decision_count', 0)))
        policy_violation_rate = round(float(metrics.get('policy_violations', 0)) / float(decision_count), 6)

        executive_metrics = {
            'decision_latency': {
                'p95_ms': decision_p95_ms,
                'target_p95_ms': int(targets['executive']['decision_latency']['target_p95_ms']),
                'compliant': decision_p95_ms <= float(targets['executive']['decision_latency']['target_p95_ms']),
            },
            'agenda_rebuild_time': {
                'p95_seconds': round(rebuild_p95_ms / 1000.0, 3),
                'target_seconds': int(targets['executive']['agenda_rebuild_time']['target_seconds']),
                'compliant': (rebuild_p95_ms / 1000.0)
                <= float(targets['executive']['agenda_rebuild_time']['target_seconds']),
            },
            'simulation_latency': {
                'p95_ms': simulation_p95_ms,
                'target_p95_ms': int(targets['executive']['simulation_latency']['target_p95_ms']),
                'compliant': simulation_p95_ms <= float(targets['executive']['simulation_latency']['target_p95_ms']),
            },
        }

        stale = dict(metrics.get('stale_intents') or {})
        intent_metrics = {
            'intent_creation_success': {
                'success_rate_percent': creation_success_rate,
                'target_percent': float(targets['intent']['intent_creation_success']['target_percent']),
                'attempts': creation_attempts,
                'successes': creation_successes,
                'compliant': creation_success_rate >= float(targets['intent']['intent_creation_success']['target_percent']),
            },
            'intent_completion_time': {
                'p95_hours': completion_p95_hours,
                'target_hours': float(targets['intent']['intent_completion_time']['target_hours']),
                'completed_count': int(metrics.get('intent_completion_count', 0)),
                'compliant': completion_p95_hours <= float(targets['intent']['intent_completion_time']['target_hours']),
            },
            'stale_intents': {
                'threshold_cycles': int(targets['intent']['stale_intents']['threshold_cycles']),
                'count': int(stale.get('count', 0)),
                'intent_ids': [str(item) for item in stale.get('intent_ids', [])],
                'compliant': int(stale.get('count', 0)) == 0,
            },
        }

        governance_metrics = {
            'policy_violation_rate': {
                'value': policy_violation_rate,
                'target': float(targets['governance']['policy_violation_rate']['target']),
                'violations': int(metrics.get('policy_violations', 0)),
                'decisions': int(metrics.get('decision_count', 0)),
                'compliant': policy_violation_rate <= float(targets['governance']['policy_violation_rate']['target']),
            },
            'simulation_state_mutations': {
                'value': int(metrics.get('simulation_state_mutations', 0)),
                'target': int(targets['governance']['simulation_state_mutations']['target']),
                'compliant': int(metrics.get('simulation_state_mutations', 0))
                <= int(targets['governance']['simulation_state_mutations']['target']),
            },
            'identity_bypass_attempts': {
                'value': int(metrics.get('identity_bypass_attempts', 0)),
                'target': int(targets['governance']['identity_bypass_attempts']['target']),
                'compliant': int(metrics.get('identity_bypass_attempts', 0))
                <= int(targets['governance']['identity_bypass_attempts']['target']),
            },
        }

        all_checks = [
            executive_metrics['decision_latency']['compliant'],
            executive_metrics['agenda_rebuild_time']['compliant'],
            executive_metrics['simulation_latency']['compliant'],
            intent_metrics['intent_creation_success']['compliant'],
            intent_metrics['intent_completion_time']['compliant'],
            intent_metrics['stale_intents']['compliant'],
            governance_metrics['policy_violation_rate']['compliant'],
            governance_metrics['simulation_state_mutations']['compliant'],
            governance_metrics['identity_bypass_attempts']['compliant'],
        ]

        return {
            'status': 'ok',
            'targets': targets,
            'metrics': {
                'executive': executive_metrics,
                'intent': intent_metrics,
                'governance': governance_metrics,
            },
            'summary': {
                'overall_compliant': all(all_checks),
                'window_size': int(metrics.get('window_size', self._operational_metric_window)),
                'current_cycle': int(metrics.get('current_cycle', 0)),
                'updated_at': str(metrics.get('updated_at', utc_now())),
            },
        }

    def budget_breach(self) -> bool:
        audits = sorted(self.store.list_cycle_audits(), key=lambda item: item.timestamp)
        if not audits:
            return False
        latest = audits[-1]
        return bool(latest.rollback_triggered)

    def stale_intent_threshold_exceeded(self) -> bool:
        snapshot = self.get_operational_slo_snapshot()
        stale = (
            snapshot.get('metrics', {})
            .get('intent', {})
            .get('stale_intents', {})
        )
        return int(stale.get('count', 0)) > 0

    def _rank_signals(
        self,
        observed_signals: List[Dict[str, Any]],
        previous_state: Dict[str, Dict[str, Any]],
        *,
        defer_threshold: int,
        policy: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []

        for item in observed_signals:
            signal_id = str(item.get('signal_id') or f"{item.get('institution_id', 'unknown')}:{item.get('type', 'signal')}")
            prior = dict(previous_state.get(signal_id) or {})
            repeat_count = int(prior.get('repeat_count', 0)) + 1
            prior_deferred = int(prior.get('deferred_count', 0))

            base_score = self._score_agenda_signal(item)
            escalation_boost = self._escalation_adjustment(
                item,
                policy=policy,
                base_score=base_score,
                deferred_count=prior_deferred,
                repeat_count=repeat_count,
            )
            effective_score = int(base_score + escalation_boost)
            status = 'ready' if effective_score >= defer_threshold else 'deferred'
            deferred_count = prior_deferred + 1 if status == 'deferred' else 0

            ranked.append(
                {
                    'signal_id': signal_id,
                    'institution_id': str(item.get('institution_id', '')),
                    'type': str(item.get('type', '')),
                    'base_score': int(base_score),
                    'escalation_boost': int(escalation_boost),
                    'score': int(effective_score),
                    'status': status,
                    'age_cycles': int(prior.get('age_cycles', 0)) + 1,
                    'deferred_count': deferred_count,
                    'repeat_count': repeat_count,
                }
            )

        ranked.sort(key=lambda entry: (-int(entry.get('score', 0)), str(entry.get('signal_id', ''))))
        return ranked

    def simulate_agenda_loop(
        self,
        signals: Optional[List[Dict[str, Any]]] = None,
        *,
        defer_threshold: int = 45,
        policy_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        observed_signals = [dict(item) for item in (signals or [])]
        previous_agenda = self.store.get_executive_agenda()
        previous_state = dict((previous_agenda.agenda_item_state if previous_agenda else {}) or {})
        policy = self._resolve_agenda_policy(policy_override)

        ranked = self._rank_signals(
            observed_signals,
            previous_state,
            defer_threshold=defer_threshold,
            policy=policy,
        )

        intents = [
            {
                'intent_type': self._intent_for_signal(item),
                'priority': int(item['score']),
                'signal_id': item['signal_id'],
            }
            for item in ranked
            if int(item.get('score', 0)) >= defer_threshold
        ]

        total_ready_score = sum(int(item['score']) for item in ranked if item['status'] == 'ready')
        budget_effects: List[Dict[str, Any]] = []
        for item in ranked:
            if item['status'] != 'ready' or total_ready_score <= 0:
                attention = 0.0
            else:
                attention = float(item['score']) / float(total_ready_score)
            budget_effects.append(
                {
                    'signal_id': item['signal_id'],
                    'attention_budget': round(attention, 4),
                    'resource_budget': round(attention * float(self.config.max_resource_cost), 3),
                }
            )

        expected_escalations = [
            {
                'signal_id': item['signal_id'],
                'escalation_boost': int(item['escalation_boost']),
                'deferred_count': int(item['deferred_count']),
                'age_cycles': int(item['age_cycles']),
            }
            for item in ranked
            if int(item.get('escalation_boost', 0)) > 0
        ]

        result = {
            'predicted_priority_order': [item['signal_id'] for item in ranked],
            'expected_escalations': expected_escalations,
            'budget_effects': budget_effects,
            'ranked_priorities': ranked,
            'intents': intents,
            'policy': policy,
            'state_mutated': False,
        }

        metrics = self._load_operational_metrics()
        metrics['simulation_runs'] = int(metrics.get('simulation_runs', 0)) + 1
        self._append_metric_value(metrics, 'simulation_latency_ms', (time.perf_counter() - started) * 1000.0)
        if bool(result.get('state_mutated')):
            metrics['simulation_state_mutations'] = int(metrics.get('simulation_state_mutations', 0)) + 1
            self._record_policy_violation(metrics)
        self._save_operational_metrics(metrics)
        return result

    def _intent_for_signal(self, signal: Dict[str, Any]) -> str:
        institution_id = str(signal.get('institution_id', '')).strip().lower()
        signal_type = str(signal.get('type', '')).strip().lower()

        if institution_id == 'sentinel' and signal_type in {'alert', 'security_alert'}:
            return 'review_sentinel_alert'
        if institution_id == 'workshop' and signal_type in {'proposal', 'tool_proposal'}:
            return 'evaluate_workshop_proposal'
        if institution_id == 'academy' and signal_type in {'research', 'research_result'}:
            return 'review_academy_research'
        return 'investigate_signal'

    def _assigned_institution_for_intent(self, signal: Dict[str, Any]) -> str:
        institution_id = str(signal.get('institution_id', '')).strip().lower()
        if institution_id:
            return institution_id
        return 'mission_control'

    def list_intent_outcomes(self, limit: int = 100) -> List[Dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        outcomes = self.store.list_intent_outcomes()
        return list(reversed(outcomes[-normalized_limit:]))

    def _apply_intent_outcome_feedback(self, intent: Intent) -> None:
        status = intent.status.value
        completion_state = str(intent.completion_state or '')
        source_priority = str(intent.source_priority or '').strip()

        outcome_event = {
            'intent_id': intent.intent_id,
            'source_priority': source_priority,
            'status': status,
            'completion_state': completion_state,
            'assigned_institution': intent.assigned_institution,
            'timestamp': utc_now(),
        }
        self.store.append_intent_outcome(outcome_event)

        agenda = self.store.get_executive_agenda()
        if agenda is None or not source_priority:
            return

        state = dict(agenda.agenda_item_state.get(source_priority) or {})
        state['outcome_events'] = int(state.get('outcome_events', 0)) + 1
        state['last_intent_status'] = status
        state['last_completion_state'] = completion_state
        state['last_outcome_at'] = outcome_event['timestamp']

        if status == IntentStatus.COMPLETED.value:
            state['completed_events'] = int(state.get('completed_events', 0)) + 1
        elif status in {IntentStatus.FAILED.value, IntentStatus.CANCELLED.value} or completion_state in {
            'stalled',
            'blocked',
        }:
            state['failed_events'] = int(state.get('failed_events', 0)) + 1
            state['needs_replan'] = True

        agenda.agenda_item_state[source_priority] = state
        for priority in agenda.priorities:
            if str(priority.get('priority_id', '')) != source_priority:
                continue
            priority['last_intent_status'] = status
            priority['last_completion_state'] = completion_state
            if state.get('needs_replan'):
                priority['feedback'] = 'needs_replan'
            elif status == IntentStatus.COMPLETED.value:
                priority['feedback'] = 'completed'
            break

        agenda.updated_at = utc_now()
        self.store.set_executive_agenda(agenda)

    def _create_intent_record(
        self,
        signal: Dict[str, Any],
        intent_type: str,
        priority_score: int,
        *,
        created_cycle: int,
    ) -> Intent:
        intent = Intent(
            intent_id=f'intent_{uuid4().hex}',
            source_priority=str(signal.get('signal_id') or ''),
            intent_type=intent_type,
            assigned_institution=self._assigned_institution_for_intent(signal),
            status=IntentStatus.CREATED,
            completion_state='pending',
            metadata={
                'priority': int(priority_score),
                'signal_type': str(signal.get('type') or ''),
                'created_cycle': int(created_cycle),
            },
        )
        return self.store.upsert_intent(intent)

    def update_intent_status(self, intent_id: str, status: str, completion_state: Optional[str] = None) -> Intent:
        intent = self.store.get_intent(intent_id)
        if intent is None:
            raise ValueError(f'unknown intent: {intent_id}')
        prior_status = intent.status
        intent.status = IntentStatus(str(status))
        if completion_state is not None:
            intent.completion_state = str(completion_state)
        intent.updated_at = utc_now()
        updated = self.store.upsert_intent(intent)

        self._apply_intent_outcome_feedback(updated)

        if prior_status != IntentStatus.COMPLETED and updated.status == IntentStatus.COMPLETED:
            metrics = self._load_operational_metrics()
            metrics['intent_completion_count'] = int(metrics.get('intent_completion_count', 0)) + 1
            try:
                created_at = datetime.fromisoformat(str(updated.created_at).replace('Z', '+00:00'))
                completed_at = datetime.fromisoformat(str(updated.updated_at).replace('Z', '+00:00'))
                elapsed_hours = max(0.0, (completed_at - created_at).total_seconds() / 3600.0)
                self._append_metric_value(metrics, 'intent_completion_hours', elapsed_hours)
            except Exception:
                pass
            self._save_operational_metrics(metrics)

        return updated

    def run_agenda_loop(self, signals: Optional[List[Dict[str, Any]]] = None, *, defer_threshold: int = 45) -> Dict[str, Any]:
        started = time.perf_counter()
        metrics = self._load_operational_metrics()
        metrics['current_cycle'] = int(metrics.get('current_cycle', 0)) + 1
        observed_signals = [dict(item) for item in (signals or [])]
        previous_agenda = self.store.get_executive_agenda()
        previous_state = dict((previous_agenda.agenda_item_state if previous_agenda else {}) or {})
        policy = self._resolve_agenda_policy()
        ranked = self._rank_signals(
            observed_signals,
            previous_state,
            defer_threshold=defer_threshold,
            policy=policy,
        )

        intents: List[Dict[str, Any]] = []
        for item in ranked:
            if int(item.get('score', 0)) < defer_threshold:
                continue
            intent_type = self._intent_for_signal(item)
            metrics['intent_creation_attempts'] = int(metrics.get('intent_creation_attempts', 0)) + 1
            intent = self._create_intent_record(
                item,
                intent_type,
                int(item['score']),
                created_cycle=int(metrics['current_cycle']),
            )
            metrics['intent_creation_successes'] = int(metrics.get('intent_creation_successes', 0)) + 1
            intents.append(
                {
                    'intent_id': intent.intent_id,
                    'intent_type': intent.intent_type,
                    'priority': int(item['score']),
                    'signal_id': item['signal_id'],
                    'assigned_institution': intent.assigned_institution,
                    'status': intent.status.value,
                }
            )

        selected = ranked[0]['signal_id'] if ranked else ''
        rejected = [item['signal_id'] for item in ranked[1:]]

        identity_allowed, identity_result = self.identity.check_action(
            action='agenda:prioritize',
            context={'signals': [item.get('signal_id', '') for item in ranked]},
        )
        governance_checks = ['within_budget']
        if len(intents) > int(self.config.max_dispatches):
            governance_checks.append('budget_dispatch_exceeded')
            intents = intents[: int(self.config.max_dispatches)]

        rationale = (
            f"selected {selected or 'none'} based on deterministic priority scoring "
            f"(sentinel=100, mission_blocker=80, workshop=50, academy=40)"
        )
        if not identity_allowed:
            rationale = f'{rationale}; identity caution: {identity_result}'
            self._record_identity_bypass_attempt(metrics)
            self._record_policy_violation(metrics)

        rebuild_started = time.perf_counter()
        agenda = self._refresh_executive_agenda()
        self._append_metric_value(metrics, 'agenda_rebuild_ms', (time.perf_counter() - rebuild_started) * 1000.0)

        total_ready_score = sum(int(item['score']) for item in ranked if item['status'] == 'ready')
        attention_budget: Dict[str, float] = {}
        resource_budget: Dict[str, float] = {}
        for item in ranked:
            signal_id = str(item['signal_id'])
            if item['status'] != 'ready':
                attention_budget[signal_id] = 0.0
                resource_budget[signal_id] = 0.0
                continue
            if total_ready_score <= 0:
                weight = 0.0
            else:
                weight = float(item['score']) / float(total_ready_score)
            attention_budget[signal_id] = round(weight, 4)
            resource_budget[signal_id] = round(weight * float(self.config.max_resource_cost), 3)

        agenda_item_state = {
            item['signal_id']: {
                'age_cycles': int(item['age_cycles']),
                'deferred_count': int(item['deferred_count']),
                'repeat_count': int(item['repeat_count']),
                'last_base_score': int(item['base_score']),
                'last_escalation_boost': int(item['escalation_boost']),
                'last_effective_score': int(item['score']),
                'last_status': str(item['status']),
            }
            for item in ranked
        }

        deferred_count_total = sum(1 for item in ranked if item['status'] == 'deferred')
        active_count_total = sum(1 for item in ranked if item['status'] == 'ready')
        blocked_count_total = sum(
            1 for item in ranked if item.get('type') == 'mission_blocker' or int(item.get('score', 0)) >= 80
        )
        if blocked_count_total > 0:
            budget_health = 'elevated'
        elif active_count_total > int(self.config.max_dispatches):
            budget_health = 'constrained'
        else:
            budget_health = 'healthy'

        agenda.priorities = [
            {
                'priority_id': item['signal_id'],
                'source': item['institution_id'] or 'signal',
                'score': int(item['score']),
                'status': item['status'],
                'base_score': int(item['base_score']),
                'escalation_boost': int(item['escalation_boost']),
                'age_cycles': int(item['age_cycles']),
                'deferred_count': int(item['deferred_count']),
            }
            for item in ranked
        ]
        agenda.strategic_priorities = [item['signal_id'] for item in ranked]
        agenda.blockers = [
            {'id': item['signal_id'], 'status': 'blocked'}
            for item in ranked
            if item.get('type') == 'mission_blocker' or int(item.get('score', 0)) >= 80
        ]
        agenda.blocked_items = [str(item['id']) for item in agenda.blockers]
        agenda.agenda_item_state = agenda_item_state
        agenda.attention_budget = attention_budget
        agenda.resource_budget = resource_budget
        agenda.budget_status = {
            **dict(agenda.budget_status or {}),
            'health': budget_health,
            'active_count': active_count_total,
            'deferred_count': deferred_count_total,
            'blocked_count': blocked_count_total,
            'policy': policy,
        }
        agenda.updated_at = utc_now()
        self.store.set_executive_agenda(agenda)

        decision = AgendaDecision(
            decision_id=f'decision_{uuid4().hex}',
            considered_inputs=[item['signal_id'] for item in ranked],
            selected_priority=selected,
            rejected_priorities=rejected,
            rationale=rationale,
            identity_checks=[identity_result],
            governance_checks=governance_checks,
            budget_impact=float(len(intents)),
            emitted_intents=[item['intent_type'] for item in intents],
        )
        self.store.append_agenda_decision(decision)
        metrics['decision_count'] = int(metrics.get('decision_count', 0)) + 1
        self._append_metric_value(metrics, 'decision_latency_ms', (time.perf_counter() - started) * 1000.0)
        self._advance_intent_health(metrics)
        self._save_operational_metrics(metrics)

        return {
            'agenda': agenda.to_dict(),
            'ranked_priorities': ranked,
            'intents': intents,
            'decision': decision.to_dict(),
        }

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
