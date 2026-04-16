from __future__ import annotations

from typing import Any, Dict, List

from .registry import registry
from .schemas import Skill


def skill_to_tool(skill: Skill) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": skill.name,
            "description": skill.description,
            "parameters": {
                "type": "object",
                "properties": skill.input_schema,
            },
        },
    }


def registry_to_tools() -> List[Dict[str, Any]]:
    return [skill_to_tool(skill) for skill in registry.list()]
