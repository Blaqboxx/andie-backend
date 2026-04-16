import json
import os
from typing import List

from .config import SKILLS_PATH, ensure_brain_dirs


def run_skill(name: str) -> List[str]:
    ensure_brain_dirs()
    path = os.path.join(SKILLS_PATH, f"{name}.json")
    with open(path, encoding="utf-8") as fp:
        skill = json.load(fp)

    executed = []
    for step in skill.get("steps", []):
        executed.append(str(step))
    return executed
