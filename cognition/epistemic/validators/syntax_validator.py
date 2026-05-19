"""
Syntax Validator — checks that a belief claim or agent output is
syntactically well-formed (non-trivial sentence, balanced brackets, etc.).
"""

from __future__ import annotations

import re
from typing import Any, List

from ..models import ValidationOutcome, ValidationResult

# Minimum token count to be considered a real claim
_MIN_TOKENS = 2
# Maximum allowed depth of unmatched brackets
_BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}"}


class SyntaxValidator:
    """
    Validates the surface-level syntax of a text claim.

    Checks performed:
    - Non-empty after stripping
    - Minimum token count
    - Balanced brackets / parentheses
    - No lone control characters

    Usage::

        validator = SyntaxValidator()
        result = validator.validate(belief.claim, target_id=belief.id)
    """

    def validate(self, text: Any, target_id: str = "unknown") -> ValidationResult:
        messages: List[str] = []
        outcome = ValidationOutcome.PASS

        claim = str(text).strip()

        if not claim:
            return ValidationResult(
                validator="syntax_validator",
                target_id=target_id,
                outcome=ValidationOutcome.FAIL,
                messages=["Claim is empty."],
                score=0.0,
            )

        # Token count
        tokens = claim.split()
        if len(tokens) < _MIN_TOKENS:
            messages.append(f"Claim too short ({len(tokens)} token(s), min={_MIN_TOKENS}).")
            outcome = ValidationOutcome.WARNING

        # Bracket balance
        stack: List[str] = []
        for ch in claim:
            if ch in _BRACKET_PAIRS:
                stack.append(_BRACKET_PAIRS[ch])
            elif ch in _BRACKET_PAIRS.values():
                if not stack or stack[-1] != ch:
                    messages.append(f"Unbalanced bracket: unexpected '{ch}'.")
                    outcome = ValidationOutcome.FAIL
                    break
                stack.pop()
        if stack:
            messages.append(f"Unclosed bracket(s): expected {''.join(stack)!r}.")
            outcome = ValidationOutcome.FAIL

        # Control characters
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", claim):
            messages.append("Claim contains illegal control characters.")
            outcome = ValidationOutcome.FAIL

        if not messages:
            messages.append("Syntax OK.")

        score = 1.0 if outcome == ValidationOutcome.PASS else (0.5 if outcome == ValidationOutcome.WARNING else 0.0)

        return ValidationResult(
            validator="syntax_validator",
            target_id=target_id,
            outcome=outcome,
            messages=messages,
            score=score,
            metadata={"token_count": len(tokens), "char_count": len(claim)},
        )
