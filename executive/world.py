from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .models import Civilization, Institution, Resource, WorldMutation
from .persistence import ExecutiveStore


@dataclass
class WorldModelEngine:
    store: ExecutiveStore

    def bootstrap_valhalla(self) -> Civilization:
        existing = self.store.get_civilization('valhalla')
        if existing is not None:
            return existing

        civilization = Civilization(
            id='valhalla',
            name='Valhalla',
            mission='Structured governed civilization runtime for ANDIE.',
        )
        self.store.upsert_civilization(civilization)

        institutions = [
            Institution('workshop', 'valhalla', 'builder', 'execute system changes', 3),
            Institution('academy', 'valhalla', 'research', 'produce knowledge assets', 2),
            Institution('laboratory', 'valhalla', 'experiment', 'run controlled experiments', 2),
            Institution('mission_control', 'valhalla', 'governance', 'coordinate proposals', 4),
            Institution('memory_vault', 'valhalla', 'memory', 'retain durable memory', 2),
            Institution('sentinel', 'valhalla', 'safety', 'enforce constraints', 5),
        ]
        for institution in institutions:
            self.store.upsert_institution(institution)

        resources = [
            Resource('gpu_time', 'compute', 1000.0, 'workshop'),
            Resource('storage', 'storage', 2000.0, 'memory_vault'),
            Resource('agent_capacity', 'capacity', 100.0, 'mission_control'),
        ]
        for resource in resources:
            self.store.upsert_resource(resource)

        return civilization

    def record_mutation(
        self,
        *,
        actor: str,
        institution: str,
        proposal_id: str,
        mutation_type: str,
        target_entity: str,
        payload: dict,
        identity_result: str,
    ) -> WorldMutation:
        mutation = WorldMutation(
            mutation_id=f'mutation_{uuid4().hex}',
            actor=actor,
            institution=institution,
            proposal_id=proposal_id,
            mutation_type=mutation_type,
            target_entity=target_entity,
            payload=dict(payload or {}),
            identity_result=identity_result,
        )
        self.store.append_world_mutation(mutation)

        if mutation_type == 'resource.update_quantity':
            resource = self.store.get_resource(target_entity)
            if resource is not None:
                quantity = float(payload.get('quantity', resource.quantity))
                resource.quantity = quantity
                self.store.upsert_resource(resource)

        return mutation
