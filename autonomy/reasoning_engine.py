
from __future__ import annotations
# =========================
# 🧠 ReasoningEngine class for orchestrator
# =========================

import json
from typing import Any, Callable, Dict, List

LLMCallable = Callable[[str], str]

class ReasoningEngine:
    def __init__(self, llm: LLMCallable = None):
        self.llm = llm

    def process(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Accepts context dict with keys: observations, memory, knowledge, goals.
        Returns: {"thought": str, "plan": {"tasks": [...]}}
        """
        # Simple thought: summarize context
        thought = f"Observations: {context.get('observations')}, Memory: {len(context.get('memory', []))} items, Goals: {context.get('goals')}"
        # Build plan using existing function
        plan_steps = build_reasoning_plan({
            "observations": context.get("observations"),
            "goals": context.get("goals"),
            "memory": context.get("memory"),
            "knowledge": context.get("knowledge"),
        }, context.get("knowledge"), self.llm)
        plan = {"tasks": [{"description": step["step"], "why": step["why"]} for step in plan_steps]}
        return {"thought": thought, "plan": plan}


LLMCallable = Callable[[str], str]


def build_reasoning_plan(
    event: Dict[str, Any],
    knowledge: Dict[str, Any] | None,
    llm: LLMCallable | None = None,
) -> List[Dict[str, str]]:
    """Create a structured step-by-step plan for an autonomy event."""
    if llm is not None:
        prompt = (
            "You are an autonomous operator. Create a step-by-step plan.\n\n"
            f"Event:\n{json.dumps(event, indent=2)}\n\n"
            f"Knowledge:\n{json.dumps(knowledge or {}, indent=2)}\n\n"
            "Return JSON list of steps with 'step' and 'why'."
        )
        try:
            raw = llm(prompt)
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
                plan: List[Dict[str, str]] = []
                for item in parsed:
                    step = str(item.get("step") or "").strip()
                    why = str(item.get("why") or "").strip()
                    if step and why:
                        plan.append({"step": step, "why": why})
                if plan:
                    return plan
        except Exception:
            pass

    return [
        {"step": "validate_event", "why": "ensure signal is valid"},
        {"step": "check_risk", "why": "avoid unsafe execution"},
        {"step": "execute", "why": "perform intended action"},
    ]


def build_multi_agent_plan(event: Dict[str, Any]) -> List[Dict[str, str]]:
    event_type = str(event.get("type") or "EVENT")
    return [
        {"agent": "signal_agent", "task": f"validate {event_type.lower()}"},
        {"agent": "risk_agent", "task": "check policy, confidence, and exposure"},
        {"agent": "execution_agent", "task": "execute or escalate the approved action"},
    ]
