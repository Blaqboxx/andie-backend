from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


LAST_DECISION_CONTEXT: Dict[str, Any] | None = None


def remember_decision_context(context: Dict[str, Any]) -> None:
    global LAST_DECISION_CONTEXT
    LAST_DECISION_CONTEXT = deepcopy(context)


def explain_decision(context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = deepcopy(context if context is not None else LAST_DECISION_CONTEXT)
    if not payload:
        return {
            "status": "empty",
            "decision": None,
            "confidence": None,
            "reasoning": [],
            "knowledge": None,
            "multiAgentPlan": [],
        }

    return {
        "status": "ok",
        "decision": payload.get("decision"),
        "confidence": payload.get("confidence"),
        "trust": payload.get("trust"),
        "reasoning": payload.get("plan", []),
        "knowledge": payload.get("knowledge_guidance"),
        "multiAgentPlan": payload.get("multi_agent_plan", []),
        "trade": payload.get("trade"),
        "event": payload.get("event"),
        "pattern": payload.get("pattern"),
    }
