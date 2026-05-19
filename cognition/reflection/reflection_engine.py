"""
Reflection Engine — orchestrates episodic memory for the ANDIE cognition layer.

ReflectionEngine is the single entry point for:
  - turning a build result + epistemic state into a ReflectionRecord
  - persisting the record via MemoryLogger
  - surfacing patterns from accumulated history via PatternDetector
  - recommending recovery strategies based on past experience
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .memory_logger import MemoryLogger
from .patterns import PatternDetector
from .reflection_models import (
    PatternSummary,
    RecoveryStrategy,
    ReflectionOutcome,
    ReflectionRecord,
)

# ---------------------------------------------------------------------------
# Recovery strategy heuristics
# ---------------------------------------------------------------------------

_STDERR_STRATEGY_MAP: List[tuple[str, RecoveryStrategy]] = [
    ("ModuleNotFoundError",     RecoveryStrategy.INSTALL_DEPS),
    ("No module named",         RecoveryStrategy.INSTALL_DEPS),
    ("SyntaxError",             RecoveryStrategy.LLM_REGEN),
    ("IndentationError",        RecoveryStrategy.LLM_REGEN),
    ("# LLM error",             RecoveryStrategy.LLM_REGEN),
    ("# LLM unavailable",       RecoveryStrategy.LLM_REGEN),
    ("TimeoutExpired",          RecoveryStrategy.RETRY_WITH_FIXES),
    ("timed out",               RecoveryStrategy.RETRY_WITH_FIXES),
    ("permission denied",       RecoveryStrategy.MANUAL_INTERVENTION),
    ("PermissionError",         RecoveryStrategy.MANUAL_INTERVENTION),
    ("ConnectionRefusedError",  RecoveryStrategy.RETRY_WITH_FIXES),
]


def _infer_recovery(stderr: str, contradictions: list[str]) -> RecoveryStrategy:
    for keyword, strategy in _STDERR_STRATEGY_MAP:
        if keyword.lower() in stderr.lower():
            return strategy
    if contradictions:
        return RecoveryStrategy.LLM_REGEN
    return RecoveryStrategy.NONE


def _infer_outcome(epistemic_status: str) -> ReflectionOutcome:
    mapping = {
        "success":                ReflectionOutcome.SUCCESS,
        "success_with_warnings":  ReflectionOutcome.PARTIAL_SUCCESS,
        "partial_success":        ReflectionOutcome.PARTIAL_SUCCESS,
        "epistemic_failure":      ReflectionOutcome.EPISTEMIC_FAILURE,
        "max_iterations_reached": ReflectionOutcome.MAX_ITERATIONS,
        "error":                  ReflectionOutcome.EXECUTION_ERROR,
    }
    return mapping.get(epistemic_status, ReflectionOutcome.UNKNOWN)


class ReflectionEngine:
    """
    Stateful reflection engine. Persists records and surfaces patterns.

    Usage::

        engine = ReflectionEngine()

        # After every build:
        record = engine.reflect(
            build_result={"task": brief, "exit_code": 0, "stdout": "...", "stderr": ""},
            epistemic_state={"status": "success", "confidence": 0.81, ...},
        )

        # Query patterns:
        patterns = engine.patterns()
    """

    def __init__(
        self,
        agent_id: str = "andie",
        log_dir: Optional[str] = None,
    ) -> None:
        self.agent_id = agent_id
        self._logger   = MemoryLogger(log_dir=log_dir)
        self._detector = PatternDetector()
        # In-process cache of this session's records (not persisted separately)
        self._session: List[ReflectionRecord] = []

    # ------------------------------------------------------------------ #
    # Core reflection                                                      #
    # ------------------------------------------------------------------ #

    def reflect(
        self,
        build_result: Dict[str, Any],
        epistemic_state: Dict[str, Any],
    ) -> ReflectionRecord:
        """
        Create, persist, and return a ReflectionRecord for a completed build.

        Parameters
        ----------
        build_result:
            Dict from the build pipeline.  Expected keys:
            ``task`` / ``brief``, ``exit_code``, ``stdout``, ``stderr``,
            ``iterations``, ``code``.
        epistemic_state:
            Dict returned by ``EpistemicEngine.evaluate()``.  Expected keys:
            ``status``, ``confidence``, ``validated``,
            ``contradictions``, ``warnings``, ``raw_status``.
        """
        task      = (
            build_result.get("task")
            or build_result.get("brief")
            or build_result.get("job_id", "unknown")
        )
        exit_code  = int(build_result.get("exit_code", -1))
        stdout     = str(build_result.get("stdout") or build_result.get("output", ""))
        stderr     = str(build_result.get("stderr") or build_result.get("error",  ""))
        iterations = int(build_result.get("iterations", 1))
        code       = str(build_result.get("code", ""))

        ep_status       = str(epistemic_state.get("status",        "unknown"))
        ep_confidence   = float(epistemic_state.get("confidence",  0.5))
        ep_validated    = bool(epistemic_state.get("validated",    False))
        ep_contradictions: list[str] = list(epistemic_state.get("contradictions", []))
        ep_warnings: list[str]       = list(epistemic_state.get("warnings",       []))
        raw_status      = str(epistemic_state.get("raw_status",    ep_status))

        # ── Failure reason ────────────────────────────────────────────
        failure_reason: Optional[str] = None
        if not ep_validated or ep_contradictions:
            if ep_contradictions:
                failure_reason = ep_contradictions[0]
            elif ep_warnings:
                failure_reason = ep_warnings[0]
            elif stderr.strip():
                failure_reason = stderr.strip()[:200]
            else:
                failure_reason = "low_confidence"

        # ── Recovery strategy ─────────────────────────────────────────
        recovery = _infer_recovery(
            stderr=(stderr + " " + (code if "# LLM" in code else "")),
            contradictions=ep_contradictions,
        )

        # ── Tags ──────────────────────────────────────────────────────
        tags: list[str] = []
        if ep_contradictions:
            tags.append("contradiction")
        if ep_warnings:
            tags.append("warning")
        if iterations > 3:
            tags.append("high_iterations")
        if exit_code != 0:
            tags.append("non_zero_exit")
        if ep_validated:
            tags.append("validated")

        record = ReflectionRecord(
            task=str(task)[:500],
            agent_id=self.agent_id,
            raw_status=raw_status,
            epistemic_status=ep_status,
            outcome=_infer_outcome(ep_status),
            confidence=ep_confidence,
            validated=ep_validated,
            exit_code=exit_code,
            iterations=iterations,
            contradictions=ep_contradictions,
            warnings=ep_warnings,
            failure_reason=failure_reason,
            recovery_strategy=recovery,
            stderr_snippet=stderr[:300],
            stdout_snippet=stdout[:300],
            tags=tags,
            metadata={
                "job_id":    build_result.get("job_id", ""),
                "build_id":  build_result.get("build_id", ""),
                "workspace": build_result.get("workspace", ""),
            },
        )

        self._logger.append(record)
        self._session.append(record)
        return record

    # ------------------------------------------------------------------ #
    # Pattern queries                                                      #
    # ------------------------------------------------------------------ #

    def patterns(
        self,
        min_occurrences: int = 2,
        since: Optional[datetime] = None,
    ) -> List[PatternSummary]:
        """Return all detected patterns from persisted history."""
        entries = (
            self._logger.filter(since=since)
            if since else self._logger.all_entries()
        )
        return self._detector.detect(entries, min_occurrences=min_occurrences)

    def top_failures(self, n: int = 5) -> List[PatternSummary]:
        """Return the n most frequent failure patterns."""
        return self._detector.top_failures(self._logger.all_entries(), n=n)

    def recent_failures(self, hours: int = 24) -> List[PatternSummary]:
        """Patterns from the last `hours` hours."""
        since = datetime.utcnow() - timedelta(hours=hours)
        return self.patterns(since=since)

    # ------------------------------------------------------------------ #
    # Adaptive hints                                                       #
    # ------------------------------------------------------------------ #

    def recommend_recovery(self, task: str) -> Optional[RecoveryStrategy]:
        """
        Look up past patterns for the given task text and recommend the
        recovery strategy that has the highest success rate.

        Returns None if no relevant pattern exists.
        """
        entries = self._logger.filter()
        relevant = [
            e for e in entries
            if task[:50].lower() in e.get("task", "").lower()
        ]
        if not relevant:
            return None

        strategy_wins: Dict[str, int] = {}
        strategy_counts: Dict[str, int] = {}
        for e in relevant:
            strat = e.get("recovery_strategy", "none")
            succeeded = e.get("recovery_succeeded")
            strategy_counts[strat] = strategy_counts.get(strat, 0) + 1
            if succeeded:
                strategy_wins[strat] = strategy_wins.get(strat, 0) + 1

        best = max(
            strategy_counts,
            key=lambda s: strategy_wins.get(s, 0) / strategy_counts[s],
            default=None,
        )
        if best and best != "none":
            try:
                return RecoveryStrategy(best)
            except ValueError:
                pass
        return None

    # ------------------------------------------------------------------ #
    # Session summary                                                      #
    # ------------------------------------------------------------------ #

    def session_summary(self) -> Dict[str, Any]:
        """Summarise this session's reflection records."""
        total      = len(self._session)
        succeeded  = sum(1 for r in self._session if r.succeeded)
        failed     = total - succeeded
        avg_conf   = (
            sum(r.confidence for r in self._session) / total if total else 0.0
        )
        return {
            "session_records": total,
            "succeeded":       succeeded,
            "failed":          failed,
            "avg_confidence":  round(avg_conf, 4),
            "tags":            [tag for r in self._session for tag in r.tags],
        }

    def mark_recovery_outcome(self, record_id: str, succeeded: bool) -> bool:
        """
        Update a persisted record's recovery_succeeded field.

        This is done by rewriting the JSONL entry in-place (scan + rewrite).
        Returns True if the record was found and updated.
        """
        entries = self._logger.all_entries()
        updated = False
        for entry in entries:
            if entry.get("id") == record_id:
                entry["recovery_succeeded"] = succeeded
                updated = True
                break
        if updated:
            import json
            log_path = self._logger._log_path
            lines = [json.dumps(e, ensure_ascii=False, default=str) for e in entries]
            with self._logger._lock:
                with log_path.open("w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
        return updated
