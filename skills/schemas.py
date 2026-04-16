from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class Skill:
    name: str
    description: str
    input_schema: Dict[str, Any]
    execute: Callable[[Dict[str, Any]], Any]
    risk_level: str = "medium"
    requires_approval: bool = False
    keywords: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
