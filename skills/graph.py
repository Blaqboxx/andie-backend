from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

from .registry import registry


@dataclass
class SkillNode:
    name: str
    depends_on: List[str] = field(default_factory=list)


class SkillGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, SkillNode] = {}

    def add_skill(self, name: str, depends_on: List[str] | None = None) -> None:
        self.nodes[name] = SkillNode(name=name, depends_on=list(depends_on or []))

    def resolve_execution_order(self, target_skill: str) -> List[str]:
        visited: Set[str] = set()
        order: List[str] = []

        def dfs(skill_name: str) -> None:
            if skill_name in visited:
                return
            visited.add(skill_name)

            node = self.nodes.get(skill_name)
            if node is not None:
                for dependency in node.depends_on:
                    dfs(dependency)

            order.append(skill_name)

        dfs(target_skill)
        return order


def build_skill_graph() -> SkillGraph:
    graph = SkillGraph()
    for skill in registry.list():
        graph.add_skill(skill.name, getattr(skill, "depends_on", []) or [])
    return graph
