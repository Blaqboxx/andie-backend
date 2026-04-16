from __future__ import annotations

from typing import Dict, List

from .schemas import Skill


class SkillRegistry:
    def __init__(self) -> None:
        self.skills: Dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self.skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def list(self) -> List[Skill]:
        return list(self.skills.values())


registry = SkillRegistry()
