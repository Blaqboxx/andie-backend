from __future__ import annotations

from typing import Any, Dict, List


def get_nested_value(payload: Dict[str, Any], field: str) -> Any:
    current: Any = payload
    for segment in field.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return None
    return current


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "==":
        return actual == expected
    if operator == "!=":
        return actual != expected

    if operator in {">", "<"}:
        try:
            left = float(actual)
            right = float(expected)
        except (TypeError, ValueError):
            return False
        return left > right if operator == ">" else left < right

    return False


def match_conditions(conditions: List[Dict[str, Any]], event: Dict[str, Any]) -> bool:
    for condition in conditions:
        field = str(condition.get("field") or "").strip()
        operator = str(condition.get("operator") or "==").strip()
        expected = condition.get("value")
        if not field:
            return False
        actual = get_nested_value(event, field)
        if not _compare(actual, operator, expected):
            return False
    return True


def match_rule(rule: Dict[str, Any], event: Dict[str, Any]) -> bool:
    when = rule.get("when") if isinstance(rule.get("when"), dict) else {}
    event_type = when.get("eventType")
    if event_type and event.get("type") != event_type:
        return False

    conditions = when.get("conditions")
    if conditions is None:
        return True
    if not isinstance(conditions, list):
        return False

    return match_conditions(conditions, event)
