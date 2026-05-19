from typing import Dict

import json
import os
from typing import Dict

class AgentWeighter:
    def __init__(self, base_weight: float = 1.0, filepath="data/weights.json"):
        self.base_weight = base_weight
        self.filepath = filepath
        self.global_weights: Dict[str, float] = {}
        self.context_weights: Dict[str, Dict[str, float]] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            with open(self.filepath, "r") as f:
                data = json.load(f)
                self.global_weights = data.get("global_weights", {})
                self.context_weights = data.get("context_weights", {})
        else:
            self.global_weights = {}
            self.context_weights = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        with open(self.filepath, "w") as f:
            json.dump({
                "global_weights": self.global_weights,
                "context_weights": self.context_weights
            }, f)

    def update_weights(self, accuracy_map: Dict[str, float], context: str = None):
        for agent, acc in accuracy_map.items():
            weight = self._scale_weight(acc)
            if context:
                if context not in self.context_weights:
                    self.context_weights[context] = {}
                self.context_weights[context][agent] = weight
            else:
                self.global_weights[agent] = weight
        self._save()

    def _scale_weight(self, accuracy: float) -> float:
        return max(0.5, min(1.5, accuracy * 2))

    def get_weight(self, agent: str, context: str = None) -> float:
        if context and context in self.context_weights:
            return self.context_weights[context].get(agent, self.base_weight)
        return self.global_weights.get(agent, self.base_weight)
