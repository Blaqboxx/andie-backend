"""
Output Validator — checks that an agent output is non-empty, within
length bounds, and does not contain forbidden patterns.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..models import ValidationOutcome, ValidationResult

# Default forbidden patterns (extend as needed)
_DEFAULT_FORBIDDEN: List[str] = [
    r"<script[^>]*>",          # XSS attempt
    r"(?i)drop\s+table",       # SQL injection
    r"(?i)ignore\s+previous",  # prompt injection
    r"(?i)disregard.*instruction",
]


class OutputValidator:
    """
    Validates a raw agent output string.

    Usage::

        validator = OutputValidator(max_length=4096)
        result = validator.validate(output_text, target_id="run-123")
    """

    def __init__(
        self,
        min_length: int = 1,
        max_length: int = 8_192,
        forbidden_patterns: Optional[List[str]] = None,
    ) -> None:
        self._min = min_length
        self._max = max_length
        patterns = (forbidden_patterns or []) + _DEFAULT_FORBIDDEN
        self._forbidden = [re.compile(p) for p in patterns]

    def validate(self, output: Any, target_id: str = "unknown") -> ValidationResult:
        """Validate a single output value."""
        messages: List[str] = []
        outcome = ValidationOutcome.PASS

        text = str(output) if not isinstance(output, str) else output

        if len(text) < self._min:
            messages.append(f"Output too short (got {len(text)}, min={self._min}).")
            outcome = ValidationOutcome.FAIL

        if len(text) > self._max:
            messages.append(f"Output too long (got {len(text)}, max={self._max}).")
            outcome = ValidationOutcome.FAIL

        for pattern in self._forbidden:
            if pattern.search(text):
                messages.append(f"Forbidden pattern detected: {pattern.pattern!r}.")
                outcome = ValidationOutcome.FAIL

        if not messages:
            messages.append("Output passed all checks.")

        score = 1.0 if outcome == ValidationOutcome.PASS else 0.0

        return ValidationResult(
            validator="output_validator",
            target_id=target_id,
            outcome=outcome,
            messages=messages,
            score=score,
            metadata={"length": len(text)},
        )
