from __future__ import annotations
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class TriggerEngine:
    def __init__(self, rules_path=None, decision_layer=None, agent_runner=None):
        self.decision = decision_layer
        self.runner = agent_runner
        self.rules: List[Dict] = []
        self._cooldown_tracker: Dict[str, float] = {}
        if rules_path:
            self._load_rules(rules_path)

    def _load_rules(self, rules_path):
        try:
            path = Path(rules_path)
            if path.exists():
                data = json.loads(path.read_text())
            if isinstance(data, list):
                self.rules = data
            elif isinstance(data, dict) and "rules" in data:
                self.rules = data["rules"]
            else:
                self.rules = [data]
        except Exception:
            self.rules = []

    def _matches_conditions(self, event: Dict, conditions: List[Dict]) -> bool:
        for cond in conditions:
            field = cond.get("field", "")
            operator = cond.get("operator", "==")
            value = cond.get("value")
            event_val = event.get(field)
            if operator == "==" and event_val != value:
                return False
            elif operator == "!=" and event_val == value:
                return False
            elif operator == ">" and not (event_val > value):
                return False
            elif operator == "<" and not (event_val < value):
                return False
        return True

    def _is_from_autonomy_pipeline(self, event: Dict) -> bool:
        metadata = event.get("metadata", {})
        return bool(metadata.get("autonomySource"))

    def _is_in_cooldown(self, rule_id: str, cooldown_ms: int) -> bool:
        if cooldown_ms <= 0:
            return False
        last = self._cooldown_tracker.get(rule_id)
        if last is None:
            return False
        elapsed_ms = (time.time() - last) * 1000
        return elapsed_ms < cooldown_ms

    async def process_event(self, event: Dict[str, Any]) -> List[Dict]:
        """Process an event against all rules. Returns list of matched+executed rules."""
        if self._is_from_autonomy_pipeline(event):
            return []

        matched = []
        event_type = event.get("type", "")

        for rule in self.rules:
            if not rule.get("enabled", True):
                continue

            when = rule.get("when", {})
            if when.get("eventType") != event_type:
                continue

            conditions = when.get("conditions", [])
            if not self._matches_conditions(event, conditions):
                continue

            rule_id = rule.get("id", "unknown")
            cooldown_ms = rule.get("cooldownMs", 0)

            if self._is_in_cooldown(rule_id, cooldown_ms):
                continue

            # Execute the action
            action = rule.get("then", {})
            if self.runner and action.get("action") == "TRIGGER_AGENT":
                agent_name = action.get("agent", "unknown")
                asyncio.ensure_future(self.runner.run(
                    agent={"id": agent_name, "name": agent_name},
                    context={"event": event},
                    trigger_id=rule_id,
                    decision={"action": "TRIGGER_AGENT"},
                ))

            self._cooldown_tracker[rule_id] = time.time()
            matched.append(rule)

        return matched

    def execute(self, task_dict):
        return {"status": "executed", "task": task_dict}
