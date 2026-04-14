from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List

from autonomy.agent_runner import AgentRunner
from autonomy.decision_engine import DecisionLayer
from autonomy.rule_evaluator import get_nested_value, match_rule


PublishEventFn = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class TriggerEngine:
    def __init__(
        self,
        *,
        rules_path: Path,
        decision_layer: DecisionLayer,
        agent_runner: AgentRunner,
        publish_event: PublishEventFn | None = None,
    ) -> None:
        self._rules_path = rules_path
        self._decision_layer = decision_layer
        self._agent_runner = agent_runner
        self._publish_event = publish_event
        self._cooldowns: Dict[str, float] = {}
        self._history = deque(maxlen=200)
        self._rules: List[Dict[str, Any]] = []
        self.reload_rules()

    @property
    def rules(self) -> List[Dict[str, Any]]:
        return list(self._rules)

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def reload_rules(self) -> List[Dict[str, Any]]:
        if not self._rules_path.exists():
            self._rules = []
            return []

        payload = json.loads(self._rules_path.read_text(encoding="utf-8"))
        rules = payload.get("rules") if isinstance(payload, dict) else None
        if not isinstance(rules, list):
            self._rules = []
            return []

        cleaned = [rule for rule in rules if isinstance(rule, dict) and rule.get("id")]
        self._rules = sorted(cleaned, key=lambda item: int(item.get("priority", 0)), reverse=True)
        return self.rules

    def _rule_in_cooldown(self, rule: Dict[str, Any]) -> bool:
        rule_id = str(rule.get("id"))
        cooldown_ms = int(rule.get("cooldownMs") or 0)
        if cooldown_ms <= 0:
            return False
        last_trigger = self._cooldowns.get(rule_id, 0.0)
        return (time.time() - last_trigger) * 1000 < cooldown_ms

    async def process_event(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if metadata.get("autonomySource"):
            return []

        triggered: List[Dict[str, Any]] = []
        for rule in self._rules:
            if not rule.get("enabled", True):
                continue
            if not match_rule(rule, event):
                continue
            if self._rule_in_cooldown(rule):
                continue

            rule_id = str(rule.get("id"))
            self._cooldowns[rule_id] = time.time()
            when = rule.get("when") if isinstance(rule.get("when"), dict) else {}
            conditions = when.get("conditions") if isinstance(when.get("conditions"), list) else []
            matched_conditions = []
            for condition in conditions:
                if not isinstance(condition, dict):
                    continue
                field = str(condition.get("field") or "").strip()
                if not field:
                    continue
                matched_conditions.append(
                    {
                        "field": field,
                        "operator": condition.get("operator", "=="),
                        "expected": condition.get("value"),
                        "actual": get_nested_value(event, field),
                    }
                )

            trigger_record = {
                "ruleId": rule_id,
                "eventType": event.get("type"),
                "matchedAt": event.get("updatedAt"),
                "matchedConditions": matched_conditions,
                "event": event,
            }
            self._history.append(trigger_record)
            triggered.append(trigger_record)

            if self._publish_event is not None:
                await self._publish_event(
                    {
                        "type": "RULE_TRIGGERED",
                        "message": f"Rule {rule_id} matched event {event.get('type')}",
                        "result": {
                            "ruleId": rule_id,
                            "eventType": event.get("type"),
                            "matchedConditions": matched_conditions,
                        },
                        "workflowId": event.get("workflowId"),
                        "taskId": event.get("taskId") or (event.get("task") or {}).get("id"),
                        "metadata": {
                            "autonomySource": "trigger_engine",
                            "ruleId": rule_id,
                            "eventType": event.get("type"),
                        },
                    }
                )

            decision = self._decision_layer.decide(
                event=event,
                trigger=rule,
                context={"recentTriggers": self.history[-10:], "recentEvents": self._decision_layer.snapshot()[-10:]},
            )

            if not decision:
                continue

            if self._publish_event is not None:
                await self._publish_event(
                    {
                        "type": "DECISION_MADE",
                        "message": decision.get("reason") or "Autonomy decision selected",
                        "decision": decision,
                        "result": {
                            "ruleId": rule_id,
                            "action": decision.get("action"),
                            "agent": decision.get("agent"),
                            "plan": decision.get("plan"),
                            "confidence": decision.get("confidence"),
                            "reason": decision.get("reason"),
                            "matchedConditions": matched_conditions,
                        },
                        "workflowId": event.get("workflowId"),
                        "taskId": event.get("taskId") or (event.get("task") or {}).get("id"),
                        "metadata": {
                            "autonomySource": "decision_layer",
                            "ruleId": rule_id,
                            "agent": decision.get("agent"),
                            "plan": decision.get("plan"),
                        },
                    }
                )

            then = rule.get("then") if isinstance(rule.get("then"), dict) else {}
            if decision.get("action") == "TRIGGER_AGENT_PLAN":
                plan = decision.get("plan") if isinstance(decision.get("plan"), list) else []
                for index, step in enumerate(plan):
                    if not isinstance(step, dict):
                        continue
                    agent_id = str(step.get("agent") or "").strip()
                    if not agent_id:
                        continue
                    await self._agent_runner.run(
                        agent={
                            "id": agent_id,
                            "policies": {
                                "maxRetries": then.get("maxRetries", 3),
                                "cooldownMs": then.get("agentCooldownMs", 10_000),
                            },
                        },
                        context={
                            "event": event,
                            "trigger": trigger_record,
                            "planStep": {
                                **step,
                                "step": step.get("step") or index + 1,
                                "totalSteps": len(plan),
                            },
                        },
                        trigger_id=rule_id,
                        decision={
                            **decision,
                            "agent": agent_id,
                            "input": step.get("input") if isinstance(step.get("input"), dict) else {},
                            "planStep": step,
                        },
                    )
                continue

            agent_id = str(decision.get("agent") or then.get("agent") or "").strip()
            if decision.get("action") != "TRIGGER_AGENT" or not agent_id:
                continue

            await self._agent_runner.run(
                agent={
                    "id": agent_id,
                    "policies": {
                        "maxRetries": then.get("maxRetries", 3),
                        "cooldownMs": then.get("agentCooldownMs", 10_000),
                    },
                },
                context={"event": event, "trigger": trigger_record},
                trigger_id=rule_id,
                decision=decision,
            )

        return triggered
