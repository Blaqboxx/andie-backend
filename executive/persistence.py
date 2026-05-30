from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (
    AgendaDecision,
    AgentCallback,
    Civilization,
    CycleAudit,
    DispatchEnvelope,
    ExecutiveAgenda,
    ExecutiveConfig,
    Goal,
    Institution,
    InstitutionProfile,
    InstitutionProposal,
    Intent,
    KnowledgeAsset,
    Mission,
    ReflectionRecord,
    Resource,
    Task,
    Treaty,
    WorldMutation,
)


class ExecutiveStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._agenda_path = self.path.parent / 'agenda.json'
        self._agenda_decisions_path = self.path.parent / 'agenda_decisions.jsonl'
        self._lock = threading.RLock()
        self._state = self._load()

    def _empty_state(self) -> Dict[str, Any]:
        return {
            'config': None,
            'civilizations': {},
            'institutions': {},
            'institution_profiles': {},
            'resources': {},
            'knowledge_assets': {},
            'treaties': {},
            'proposals': {},
            'world_mutations': {},
            'missions': {},
            'goals': {},
            'tasks': {},
            'intents': {},
            'reflections': {},
            'dispatches': {},
            'callbacks': {},
            'cycle_log': [],
            'cycle_audits': {},
        }

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._empty_state()
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            baseline = self._empty_state()
            baseline.update({key: data.get(key, baseline[key]) for key in baseline})
            return baseline
        except Exception:
            return self._empty_state()

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._state, indent=2, sort_keys=True), encoding='utf-8')

    def set_config(self, config: ExecutiveConfig) -> None:
        with self._lock:
            self._state['config'] = config.to_dict()
            self._save()

    def get_config(self) -> Optional[ExecutiveConfig]:
        raw = self._state.get('config')
        return ExecutiveConfig.from_dict(raw) if isinstance(raw, dict) else None

    def set_executive_agenda(self, agenda: ExecutiveAgenda) -> ExecutiveAgenda:
        with self._lock:
            self._state['executive_agenda'] = agenda.to_dict()
            self._agenda_path.write_text(json.dumps(agenda.to_dict(), indent=2, sort_keys=True), encoding='utf-8')
            self._save()
            return agenda

    def get_executive_agenda(self) -> Optional[ExecutiveAgenda]:
        raw = self._state.get('executive_agenda')
        if not isinstance(raw, dict) and self._agenda_path.exists():
            try:
                raw = json.loads(self._agenda_path.read_text(encoding='utf-8'))
            except Exception:
                raw = None
        return ExecutiveAgenda.from_dict(raw) if isinstance(raw, dict) else None

    def append_agenda_decision(self, decision: AgendaDecision) -> AgendaDecision:
        with self._lock:
            with self._agenda_decisions_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(decision.to_dict(), sort_keys=True) + '\n')
            return decision

    def list_agenda_decisions(self) -> List[AgendaDecision]:
        if not self._agenda_decisions_path.exists():
            return []
        decisions: List[AgendaDecision] = []
        for line in self._agenda_decisions_path.read_text(encoding='utf-8').splitlines():
            payload = line.strip()
            if not payload:
                continue
            try:
                decisions.append(AgendaDecision.from_dict(json.loads(payload)))
            except Exception:
                continue
        return decisions

    def upsert_civilization(self, civilization: Civilization) -> Civilization:
        with self._lock:
            self._state['civilizations'][civilization.id] = civilization.to_dict()
            self._save()
            return civilization

    def get_civilization(self, civilization_id: str) -> Optional[Civilization]:
        raw = self._state['civilizations'].get(civilization_id)
        return Civilization.from_dict(raw) if isinstance(raw, dict) else None

    def list_civilizations(self) -> List[Civilization]:
        return [Civilization.from_dict(item) for item in self._state['civilizations'].values()]

    def upsert_institution(self, institution: Institution) -> Institution:
        with self._lock:
            self._state['institutions'][institution.id] = institution.to_dict()
            self._save()
            return institution

    def list_institutions(self, civilization_id: str | None = None) -> List[Institution]:
        institutions = [Institution.from_dict(item) for item in self._state['institutions'].values()]
        if civilization_id is None:
            return institutions
        return [item for item in institutions if item.civilization_id == civilization_id]

    def upsert_institution_profile(self, profile: InstitutionProfile) -> InstitutionProfile:
        with self._lock:
            self._state['institution_profiles'][profile.institution_id] = profile.to_dict()
            self._save()
            return profile

    def get_institution_profile(self, institution_id: str) -> Optional[InstitutionProfile]:
        raw = self._state['institution_profiles'].get(institution_id)
        return InstitutionProfile.from_dict(raw) if isinstance(raw, dict) else None

    def list_institution_profiles(self) -> List[InstitutionProfile]:
        return [InstitutionProfile.from_dict(item) for item in self._state['institution_profiles'].values()]

    def upsert_resource(self, resource: Resource) -> Resource:
        with self._lock:
            self._state['resources'][resource.id] = resource.to_dict()
            self._save()
            return resource

    def get_resource(self, resource_id: str) -> Optional[Resource]:
        raw = self._state['resources'].get(resource_id)
        return Resource.from_dict(raw) if isinstance(raw, dict) else None

    def list_resources(self) -> List[Resource]:
        return [Resource.from_dict(item) for item in self._state['resources'].values()]

    def upsert_knowledge_asset(self, asset: KnowledgeAsset) -> KnowledgeAsset:
        with self._lock:
            self._state['knowledge_assets'][asset.id] = asset.to_dict()
            self._save()
            return asset

    def list_knowledge_assets(self) -> List[KnowledgeAsset]:
        return [KnowledgeAsset.from_dict(item) for item in self._state['knowledge_assets'].values()]

    def upsert_treaty(self, treaty: Treaty) -> Treaty:
        with self._lock:
            self._state['treaties'][treaty.id] = treaty.to_dict()
            self._save()
            return treaty

    def list_treaties(self) -> List[Treaty]:
        return [Treaty.from_dict(item) for item in self._state['treaties'].values()]

    def upsert_proposal(self, proposal: InstitutionProposal) -> InstitutionProposal:
        with self._lock:
            self._state['proposals'][proposal.proposal_id] = proposal.to_dict()
            self._save()
            return proposal

    def get_proposal(self, proposal_id: str) -> Optional[InstitutionProposal]:
        raw = self._state['proposals'].get(proposal_id)
        return InstitutionProposal.from_dict(raw) if isinstance(raw, dict) else None

    def list_proposals(self, status: str | None = None) -> List[InstitutionProposal]:
        proposals = [InstitutionProposal.from_dict(item) for item in self._state['proposals'].values()]
        if status is None:
            return proposals
        return [proposal for proposal in proposals if proposal.status.value == status]

    def append_world_mutation(self, mutation: WorldMutation) -> WorldMutation:
        with self._lock:
            self._state['world_mutations'][mutation.mutation_id] = mutation.to_dict()
            self._save()
            return mutation

    def list_world_mutations(self) -> List[WorldMutation]:
        return [WorldMutation.from_dict(item) for item in self._state['world_mutations'].values()]

    def upsert_mission(self, mission: Mission) -> Mission:
        with self._lock:
            self._state['missions'][mission.mission_id] = mission.to_dict()
            self._save()
            return mission

    def get_mission(self, mission_id: str) -> Optional[Mission]:
        raw = self._state['missions'].get(mission_id)
        return Mission.from_dict(raw) if isinstance(raw, dict) else None

    def upsert_goal(self, goal: Goal) -> Goal:
        with self._lock:
            self._state['goals'][goal.goal_id] = goal.to_dict()
            self._save()
            return goal

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        raw = self._state['goals'].get(goal_id)
        return Goal.from_dict(raw) if isinstance(raw, dict) else None

    def list_goals(self, status: str | None = None) -> List[Goal]:
        goals = [Goal.from_dict(item) for item in self._state['goals'].values()]
        if status is None:
            return goals
        return [goal for goal in goals if goal.status.value == status]

    def upsert_task(self, task: Task) -> Task:
        with self._lock:
            self._state['tasks'][task.task_id] = task.to_dict()
            self._save()
            return task

    def upsert_intent(self, intent: Intent) -> Intent:
        with self._lock:
            self._state['intents'][intent.intent_id] = intent.to_dict()
            self._save()
            return intent

    def get_intent(self, intent_id: str) -> Optional[Intent]:
        raw = self._state['intents'].get(intent_id)
        return Intent.from_dict(raw) if isinstance(raw, dict) else None

    def list_intents(self, status: str | None = None) -> List[Intent]:
        intents = [Intent.from_dict(item) for item in self._state['intents'].values()]
        if status is None:
            return intents
        return [intent for intent in intents if intent.status.value == status]

    def get_task(self, task_id: str) -> Optional[Task]:
        raw = self._state['tasks'].get(task_id)
        return Task.from_dict(raw) if isinstance(raw, dict) else None

    def list_tasks(self, goal_id: str | None = None) -> List[Task]:
        tasks = [Task.from_dict(item) for item in self._state['tasks'].values()]
        if goal_id is None:
            return tasks
        return [task for task in tasks if task.goal_id == goal_id]

    def upsert_reflection(self, reflection: ReflectionRecord) -> ReflectionRecord:
        with self._lock:
            self._state['reflections'][reflection.reflection_id] = reflection.to_dict()
            self._save()
            return reflection

    def list_reflections(self, goal_id: str | None = None) -> List[ReflectionRecord]:
        reflections = [ReflectionRecord.from_dict(item) for item in self._state['reflections'].values()]
        if goal_id is None:
            return reflections
        return [reflection for reflection in reflections if reflection.goal_id == goal_id]

    def record_dispatch(self, envelope: DispatchEnvelope) -> DispatchEnvelope:
        with self._lock:
            self._state['dispatches'][envelope.envelope_id] = envelope.to_dict()
            self._save()
            return envelope

    def record_callback(self, callback: AgentCallback) -> AgentCallback:
        with self._lock:
            self._state['callbacks'][callback.callback_id] = callback.to_dict()
            self._save()
            return callback

    def append_cycle_log(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._state['cycle_log'].append(entry)
            self._state['cycle_log'] = self._state['cycle_log'][-200:]
            self._save()

    def append_cycle_audit(self, audit: CycleAudit) -> CycleAudit:
        with self._lock:
            self._state['cycle_audits'][audit.cycle_id] = audit.to_dict()
            self._save()
            return audit

    def list_cycle_audits(self) -> List[CycleAudit]:
        return [CycleAudit.from_dict(item) for item in self._state['cycle_audits'].values()]
