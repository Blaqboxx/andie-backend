"""
Pattern Detector — mines ReflectionRecords for recurring failure modes
and success patterns that ANDIE can use to make better decisions.

All detection is statistical (no LLM required) and operates on raw log
dicts so it can run directly on persisted JSONL entries from MemoryLogger.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .reflection_models import PatternSummary, RecoveryStrategy

# ---------------------------------------------------------------------------
# Failure keyword taxonomy
# ---------------------------------------------------------------------------

_FAILURE_TAXONOMY: List[Tuple[str, str]] = [
    # (keyword_in_failure_reason_or_stderr,  canonical_label)
    ("ModuleNotFoundError",     "missing_python_dependency"),
    ("No module named",         "missing_python_dependency"),
    ("permission denied",       "permission_error"),
    ("PermissionError",         "permission_error"),
    ("Traceback",               "unhandled_exception"),
    ("SyntaxError",             "syntax_error"),
    ("IndentationError",        "syntax_error"),
    ("TimeoutExpired",          "execution_timeout"),
    ("timed out",               "execution_timeout"),
    ("LLM error",               "llm_failure"),
    ("LLM unavailable",         "llm_failure"),
    ("exit_code=1",             "test_failure"),
    ("FAILED",                  "test_failure"),
    ("AssertionError",          "assertion_failure"),
    ("ConnectionRefusedError",  "network_error"),
    ("docker",                  "docker_error"),
    ("Docker",                  "docker_error"),
    ("contradiction",           "epistemic_contradiction"),
    ("low_confidence",          "low_confidence"),
]

_MIN_OCCURRENCES = 2   # minimum events before a pattern is surfaced
_HIGH_FAILURE_THRESHOLD = 0.5   # failure rate above this triggers a hint


def _classify_failure(text: str) -> str:
    """Map a free-text failure reason to a canonical label."""
    if not text:
        return "unknown"
    for keyword, label in _FAILURE_TAXONOMY:
        if keyword.lower() in text.lower():
            return label
    return "unknown"


def _actionable_hint(label: str, recovery: str) -> str:
    hints: Dict[str, str] = {
        "missing_python_dependency": (
            "Pre-install dependencies before execution or add a pip-install step."
        ),
        "permission_error": (
            "Check file/directory permissions or run with elevated privileges."
        ),
        "unhandled_exception": (
            "Add try/except blocks and improve error handling in generated code."
        ),
        "syntax_error": (
            "Validate generated code with a syntax validator before execution."
        ),
        "execution_timeout": (
            "Increase timeout budget or break the task into smaller sub-tasks."
        ),
        "llm_failure": (
            "LLM is unreachable — use a fallback plan or deterministic templates."
        ),
        "test_failure": (
            "Generated tests are too strict or code is incomplete; review prompts."
        ),
        "assertion_failure": (
            "Assertions in tests do not match generated code behaviour; relax test constraints."
        ),
        "network_error": (
            "Check network connectivity or mock external calls during build."
        ),
        "docker_error": (
            "Verify Docker daemon is running and the user has Docker socket access."
        ),
        "epistemic_contradiction": (
            "Review belief sources — evidence conflicts may indicate unreliable input data."
        ),
        "low_confidence": (
            "Collect more corroborating evidence before trusting this outcome."
        ),
    }
    return hints.get(label, "Investigate recurring failures in this category.")


class PatternDetector:
    """
    Scans a list of log entry dicts (from MemoryLogger) and produces
    PatternSummary objects describing recurring failure modes.

    Usage::

        detector = PatternDetector()
        patterns = detector.detect(logger.all_entries())
        for p in patterns:
            print(p.label, p.failure_rate, p.actionable_hint)
    """

    def detect(
        self,
        entries: List[Dict[str, Any]],
        min_occurrences: int = _MIN_OCCURRENCES,
    ) -> List[PatternSummary]:
        """
        Analyse log entries and return a list of PatternSummary objects.

        Only patterns that appear at least `min_occurrences` times are returned.
        Results are sorted by failure_rate descending.
        """
        if not entries:
            return []

        # Group entries by canonical failure label
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            reason = entry.get("failure_reason") or ""
            stderr = entry.get("stderr_snippet") or ""
            label  = _classify_failure(reason) or _classify_failure(stderr)
            groups[label].append(entry)

        summaries: List[PatternSummary] = []
        for label, group in groups.items():
            if len(group) < min_occurrences:
                continue

            total       = len(group)
            failed      = sum(
                1 for e in group
                if e.get("epistemic_status") not in ("success", "success_with_warnings")
            )
            failure_rate = round(failed / total, 4)

            # Average metrics
            confidences = [float(e.get("confidence", 0.5)) for e in group]
            iterations  = [int(e.get("iterations", 1))      for e in group]
            avg_conf    = round(sum(confidences) / total, 4)
            avg_iter    = round(sum(iterations)  / total, 2)

            # Dominant recovery strategy
            strategies = [e.get("recovery_strategy", "none") for e in group]
            dominant_recovery_str = Counter(strategies).most_common(1)[0][0]
            try:
                dominant_recovery = RecoveryStrategy(dominant_recovery_str)
            except ValueError:
                dominant_recovery = RecoveryStrategy.NONE

            # Recovery success rate
            with_recovery = [
                e for e in group
                if e.get("recovery_strategy", "none") != "none"
                and e.get("recovery_succeeded") is not None
            ]
            if with_recovery:
                rec_success_rate = round(
                    sum(1 for e in with_recovery if e.get("recovery_succeeded")) / len(with_recovery), 4
                )
            else:
                rec_success_rate = 0.0

            # Dominant failure reason (raw text)
            reasons = [e.get("failure_reason") or "" for e in group if e.get("failure_reason")]
            dominant_reason = Counter(reasons).most_common(1)[0][0] if reasons else None

            # Timestamps
            timestamps: List[datetime] = []
            for e in group:
                try:
                    timestamps.append(datetime.fromisoformat(e["timestamp"]))
                except (KeyError, ValueError):
                    pass
            first_seen = min(timestamps) if timestamps else datetime.utcnow()
            last_seen  = max(timestamps) if timestamps else datetime.utcnow()

            # Example tasks (up to 3 unique)
            task_counter = Counter(e.get("task", "")[:100] for e in group)
            example_tasks = [t for t, _ in task_counter.most_common(3) if t]

            hint = _actionable_hint(label, dominant_recovery_str)

            summaries.append(PatternSummary(
                label=label,
                occurrences=total,
                failure_rate=failure_rate,
                avg_confidence=avg_conf,
                avg_iterations=avg_iter,
                dominant_failure_reason=dominant_reason,
                dominant_recovery=dominant_recovery,
                recovery_success_rate=rec_success_rate,
                example_tasks=example_tasks,
                first_seen=first_seen,
                last_seen=last_seen,
                actionable_hint=hint,
            ))

        summaries.sort(key=lambda s: s.failure_rate, reverse=True)
        return summaries

    def top_failures(
        self,
        entries: List[Dict[str, Any]],
        n: int = 5,
    ) -> List[PatternSummary]:
        """Return the top-n highest-failure-rate patterns."""
        return self.detect(entries)[:n]

    def success_patterns(
        self,
        entries: List[Dict[str, Any]],
    ) -> List[PatternSummary]:
        """Return patterns that have a low failure rate (what's working)."""
        return [p for p in self.detect(entries) if p.failure_rate < _HIGH_FAILURE_THRESHOLD]
