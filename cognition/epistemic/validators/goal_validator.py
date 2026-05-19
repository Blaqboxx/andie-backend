"""
Goal Validator — checks whether a belief or agent output is aligned
with the currently active goal set.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..models import Belief, ValidationOutcome, ValidationResult


class GoalValidator:
    """
    Validates that a belief or output claim is consistent with one or
    more active goals.

    Goals are plain-text descriptions or structured dicts with a
    ``goal`` key and optional ``keywords`` list.

    Usage::

        validator = GoalValidator(goals=["maintain system health", "serve user requests"])
        result = validator.validate(belief)
    """

    def __init__(self, goals: Optional[List[Any]] = None) -> None:
        self._goals: List[Dict[str, Any]] = []
        for g in (goals or []):
            self._goals.append(self._normalize(g))

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def add_goal(self, goal: Any) -> None:
        """Register an additional goal."""
        self._goals.append(self._normalize(goal))

    def validate(self, belief: Belief) -> ValidationResult:
        """Return a ValidationResult for the given belief against all goals."""
        if not self._goals:
            return ValidationResult(
                validator="goal_validator",
                target_id=belief.id,
                outcome=ValidationOutcome.SKIPPED,
                messages=["No goals registered."],
            )

        matched, unmatched = [], []
        claim_lower = belief.claim.lower()

        for goal in self._goals:
            keywords: List[str] = goal.get("keywords", [])
            goal_text: str = goal.get("goal", "")

            # At least one keyword from the goal must appear in the claim
            hits = [kw for kw in keywords if kw in claim_lower]
            if hits or any(w in claim_lower for w in goal_text.lower().split()):
                matched.append(goal_text)
            else:
                unmatched.append(goal_text)

        if matched:
            outcome = ValidationOutcome.PASS
            messages = [f"Aligned with: {', '.join(matched)}"]
        else:
            outcome = ValidationOutcome.WARNING
            messages = [f"No alignment found with goals: {', '.join(unmatched)}"]

        return ValidationResult(
            validator="goal_validator",
            target_id=belief.id,
            outcome=outcome,
            messages=messages,
            score=len(matched) / max(len(self._goals), 1),
            metadata={"matched": matched, "unmatched": unmatched},
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize(goal: Any) -> Dict[str, Any]:
        if isinstance(goal, str):
            return {"goal": goal, "keywords": goal.lower().split()}
        if isinstance(goal, dict):
            g = dict(goal)
            if "keywords" not in g:
                g["keywords"] = g.get("goal", "").lower().split()
            return g
        return {"goal": str(goal), "keywords": str(goal).lower().split()}
