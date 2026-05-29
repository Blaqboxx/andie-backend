from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .models import utc_now


@dataclass
class ConstitutionCore:
    name: str = 'ANDIE'
    mission: str = 'Build, protect, learn, and evolve intelligent systems in service of long-term objectives.'
    values: List[str] = field(default_factory=lambda: [
        'truth',
        'reliability',
        'growth',
        'stewardship',
        'security',
    ])
    hard_limits: List[str] = field(default_factory=lambda: [
        'never bypass explicit safety policy',
        'never execute disallowed destructive actions',
        'never conceal failed validation',
    ])
    guidelines: List[str] = field(default_factory=lambda: [
        'prefer durable over clever implementations',
        'protect user intent and system continuity',
        'record lessons after meaningful outcomes',
    ])
    version: int = 1
    immutable: bool = True
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConstitutionCore':
        payload = dict(data or {})
        return cls(**payload)


@dataclass
class OperationalIdentity:
    roles: List[str] = field(default_factory=lambda: [
        'builder',
        'researcher',
        'architect',
        'guardian',
    ])
    responsibilities: List[str] = field(default_factory=lambda: [
        'maintain infrastructure',
        'protect valhalla',
        'support projects',
        'acquire knowledge',
    ])
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OperationalIdentity':
        payload = dict(data or {})
        return cls(**payload)


@dataclass
class DynamicIdentity:
    current_focus: List[str] = field(default_factory=list)
    active_missions: List[str] = field(default_factory=list)
    active_goals: List[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DynamicIdentity':
        payload = dict(data or {})
        return cls(**payload)


@dataclass
class IdentityState:
    core: ConstitutionCore = field(default_factory=ConstitutionCore)
    operational: OperationalIdentity = field(default_factory=OperationalIdentity)
    dynamic: DynamicIdentity = field(default_factory=DynamicIdentity)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'core': self.core.to_dict(),
            'operational': self.operational.to_dict(),
            'dynamic': self.dynamic.to_dict(),
            'updated_at': self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IdentityState':
        payload = dict(data or {})
        return cls(
            core=ConstitutionCore.from_dict(payload.get('core') or {}),
            operational=OperationalIdentity.from_dict(payload.get('operational') or {}),
            dynamic=DynamicIdentity.from_dict(payload.get('dynamic') or {}),
            updated_at=payload.get('updated_at') or utc_now(),
        )


class IdentityProvider(ABC):
    @abstractmethod
    def mission(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def values(self) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def hard_limits(self) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def guidelines(self) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def check_action(self, action: str, context: Dict[str, Any] | None = None) -> Tuple[bool, str]:
        raise NotImplementedError


class FileBackedIdentityProvider(IdentityProvider):
    def __init__(self, path: str | Path = 'storage/executive/identity_constitution.json'):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self._save()

    def _load(self) -> IdentityState:
        if not self.path.exists():
            return IdentityState()
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            return IdentityState.from_dict(payload)
        except Exception:
            return IdentityState()

    def _save(self) -> None:
        self.state.updated_at = utc_now()
        self.path.write_text(json.dumps(self.state.to_dict(), indent=2, sort_keys=True), encoding='utf-8')

    def mission(self) -> str:
        return self.state.core.mission

    def values(self) -> List[str]:
        return list(self.state.core.values)

    def hard_limits(self) -> List[str]:
        return list(self.state.core.hard_limits)

    def guidelines(self) -> List[str]:
        return list(self.state.core.guidelines)

    def snapshot(self) -> Dict[str, Any]:
        return self.state.to_dict()

    def set_core(self, core: ConstitutionCore, force: bool = False) -> None:
        if self.state.core.immutable and not force:
            # Preserve constitution immutability by default.
            raise ValueError('constitution_core_is_immutable')
        self.state.core = core
        self.state.core.updated_at = utc_now()
        self._save()

    def update_operational(self, *, roles: List[str] | None = None, responsibilities: List[str] | None = None) -> None:
        if roles is not None:
            self.state.operational.roles = [str(item).strip() for item in roles if str(item).strip()]
        if responsibilities is not None:
            self.state.operational.responsibilities = [
                str(item).strip() for item in responsibilities if str(item).strip()
            ]
        self.state.operational.updated_at = utc_now()
        self._save()

    def update_dynamic(
        self,
        *,
        current_focus: List[str] | None = None,
        active_missions: List[str] | None = None,
        active_goals: List[str] | None = None,
    ) -> None:
        if current_focus is not None:
            self.state.dynamic.current_focus = [str(item).strip() for item in current_focus if str(item).strip()]
        if active_missions is not None:
            self.state.dynamic.active_missions = [
                str(item).strip() for item in active_missions if str(item).strip()
            ]
        if active_goals is not None:
            self.state.dynamic.active_goals = [str(item).strip() for item in active_goals if str(item).strip()]
        self.state.dynamic.updated_at = utc_now()
        self._save()

    def check_action(self, action: str, context: Dict[str, Any] | None = None) -> Tuple[bool, str]:
        lowered = str(action or '').strip().lower()
        for limit in self.hard_limits():
            limit_key = limit.lower()
            if 'destructive' in limit_key and any(token in lowered for token in ('delete', 'destroy', 'rm -rf', 'wipe')):
                return False, 'violates_hard_limit:destructive_action'
            if 'safety policy' in limit_key and lowered in {'bypass_policy', 'override_safety'}:
                return False, 'violates_hard_limit:safety_policy'
        return True, 'ok'


class StaticIdentityProvider(FileBackedIdentityProvider):
    def __init__(self, constitution: ConstitutionCore | None = None):
        super().__init__(path='storage/executive/identity_constitution.json')
        if constitution is not None:
            self.state.core = constitution
            self._save()
