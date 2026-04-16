from __future__ import annotations

import os
from collections import deque
from typing import Any, Deque, Dict, List

from autonomy.learning import detect_pattern


AUTONOMY_MAX_CONFIDENCE_REQUIRED = float(os.environ.get("AUTONOMY_MAX_CONFIDENCE_REQUIRED", "0.6"))


def weighted_decision(context: Dict[str, Any]) -> str:
    """Map confidence + knowledge strength to EXECUTE/REVIEW/BLOCK."""
    knowledge = context.get("knowledge_guidance") if isinstance(context.get("knowledge_guidance"), dict) else {}
    confidence = float(context.get("confidence", 0.5) or 0.5)
    pattern = context.get("pattern")

    if pattern == "HIGH_FAILURE_RATE":
        return "BLOCK"

    has_relevant_knowledge = bool(knowledge.get("relevant"))
    min_execute = max(AUTONOMY_MAX_CONFIDENCE_REQUIRED, 0.7)

    if has_relevant_knowledge and confidence >= min_execute:
        return "EXECUTE"
    if confidence >= 0.35:
        return "REVIEW"
    return "BLOCK"


class DecisionLayer:
    """Rule + heuristic hybrid for selecting autonomy actions."""

    def __init__(self, memory_limit: int = 20) -> None:
        self._events: Deque[Dict[str, Any]] = deque(maxlen=max(memory_limit, 1))

    def snapshot(self) -> List[Dict[str, Any]]:
        return list(self._events)

    def decide(
        self,
        *,
        event: Dict[str, Any],
        trigger: Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any] | None:
        self._events.append(event)
        context = context or {}
        pattern = detect_pattern(context.get("recentLearningEvents", [])) if isinstance(context.get("recentLearningEvents"), list) else None
        if pattern == "HIGH_FAILURE_RATE":
            return {
                "action": "BLOCK",
                "confidence": 0.2,
                "reason": "Recent learning history shows a high failure rate",
                "pattern": pattern,
            }

        then = trigger.get("then") if isinstance(trigger.get("then"), dict) else {}
        action = then.get("action")
        plan_agents = then.get("agents") if isinstance(then.get("agents"), list) else []
        if action == "TRIGGER_AGENT_PLAN" or plan_agents:
            plan = []
            for index, agent_entry in enumerate(plan_agents):
                if isinstance(agent_entry, dict):
                    agent_name = agent_entry.get("agent")
                    agent_input = agent_entry.get("input") if isinstance(agent_entry.get("input"), dict) else {}
                    role = agent_entry.get("role")
                else:
                    agent_name = agent_entry
                    agent_input = {}
                    role = None
                if not agent_name:
                    continue
                plan.append(
                    {
                        "agent": agent_name,
                        "input": agent_input,
                        "role": role,
                        "step": index + 1,
                    }
                )
            if plan:
                return {
                    "action": "TRIGGER_AGENT_PLAN",
                    "plan": plan,
                    "confidence": 0.92,
                    "reason": f"Rule {trigger.get('id', 'unknown')} matched multi-agent plan",
                }

        if action == "TRIGGER_AGENT":
            return {
                "action": "TRIGGER_AGENT",
                "agent": then.get("agent"),
                "input": then.get("input") if isinstance(then.get("input"), dict) else {},
                "confidence": 0.9,
                "reason": f"Rule {trigger.get('id', 'unknown')} matched",
            }

        event_type = str(event.get("type") or "")
        if event_type in {"STREAM_ERROR", "TASK_FAILURE", "ERROR"}:
            return {
                "action": "TRIGGER_AGENT",
                "agent": "self_healing_agent",
                "input": {"reason": f"{event_type} detected", "event": event},
                "confidence": 0.75,
                "reason": "Reactive error handling heuristic",
            }

        repeated_failures = [
            entry
            for entry in self._events
            if str(entry.get("type") or "") in {"TASK_FAILURE", "STREAM_ERROR", "ERROR"}
        ]
        if len(repeated_failures) >= 3:
            return {
                "action": "TRIGGER_AGENT_PLAN",
                "plan": [
                    {
                        "agent": "diagnostic_agent",
                        "role": "diagnose",
                        "step": 1,
                        "input": {
                            "reason": "Repeated failures detected",
                            "recentFailures": repeated_failures[-3:],
                            "context": context,
                        },
                    },
                    {
                        "agent": "self_healing_agent",
                        "role": "recover",
                        "step": 2,
                        "input": {
                            "reason": "Execute recovery after diagnostics",
                            "recentFailures": repeated_failures[-3:],
                        },
                    },
                ],
                "confidence": 0.86,
                "reason": "Repeated failure heuristic escalated to multi-agent plan",
            }

        return None
