"""
Runtime Validator — checks live system/agent state against expected thresholds.

Accepts a snapshot dict (e.g. from a health endpoint) and asserts that
key metrics are within acceptable bounds.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..models import ValidationOutcome, ValidationResult

# Default thresholds
_DEFAULTS: Dict[str, Tuple[float, float]] = {
    "confidence": (0.25, 1.0),
    "active_beliefs": (0.0, 500.0),
    "contradiction_count": (0.0, 5.0),
    "cpu_percent": (0.0, 90.0),
    "memory_percent": (0.0, 85.0),
}


class RuntimeValidator:
    """
    Validates a runtime metrics snapshot.

    Usage::

        validator = RuntimeValidator()
        metrics = {"confidence": 0.3, "contradiction_count": 1}
        result = validator.validate(metrics, target_id="andie-runtime")
    """

    def __init__(
        self,
        thresholds: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> None:
        self._thresholds = dict(_DEFAULTS)
        if thresholds:
            self._thresholds.update(thresholds)

    def validate(
        self,
        metrics: Dict[str, Any],
        target_id: str = "runtime",
    ) -> ValidationResult:
        messages: List[str] = []
        failures = 0
        warnings = 0

        for key, (lo, hi) in self._thresholds.items():
            if key not in metrics:
                continue
            try:
                val = float(metrics[key])
            except (TypeError, ValueError):
                messages.append(f"{key}: non-numeric value {metrics[key]!r}.")
                warnings += 1
                continue

            if val < lo:
                messages.append(f"{key}={val:.3f} below minimum {lo}.")
                failures += 1
            elif val > hi:
                messages.append(f"{key}={val:.3f} exceeds maximum {hi}.")
                failures += 1
            else:
                messages.append(f"{key}={val:.3f} OK.")

        if failures:
            outcome = ValidationOutcome.FAIL
        elif warnings:
            outcome = ValidationOutcome.WARNING
        else:
            outcome = ValidationOutcome.PASS

        total = len(self._thresholds)
        score = max(0.0, (total - failures) / total) if total else 1.0

        return ValidationResult(
            validator="runtime_validator",
            target_id=target_id,
            outcome=outcome,
            messages=messages or ["All runtime metrics within bounds."],
            score=round(score, 4),
            metadata={"metrics_checked": list(self._thresholds.keys()), "failures": failures},
        )
