"""
Retry Engine — adaptive retry orchestrator for ANDIE's recovery subsystem.

The RetryEngine is the executive layer of STEP 5.  It:

  1. Accepts a failed build result + epistemic state
  2. Selects a RecoveryStrategy via StrategySelector
  3. Executes the strategy (generating a modified run_command / prompt patch)
  4. Returns a RetryResult so the build loop can act on it
  5. Feeds the outcome back to ReflectionEngine for future learning

Strategies implemented here are *command-level* adaptations — they modify
what the next build iteration will run and/or inject context into the LLM
prompt.  Heavy code-rewriting is handled by the LLM (REGEN strategy).
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, List, Optional

from .recovery_models import RecoveryStrategy, RetryContext, RetryResult
from .strategy_selector import StrategySelector

PY_BIN = sys.executable or "python3"


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_REGEN_PROMPT_TEMPLATE = """\
Previous build attempt failed. Generate a corrected implementation.

Task:
{task}

Failure reason:
{failure_reason}

Stderr (last run):
{stderr_snippet}

Stdout (last run):
{stdout_snippet}

Epistemic confidence: {confidence:.2f}

Contradictions detected:
{contradictions}

Prior recovery attempts:
{prior_summary}

Instructions:
- Address every failure reason listed above.
- Do NOT repeat patterns that already failed.
- Return only a JSON object: {{"files": {{"path": "content"}}}}.
"""

_REDUCE_SCOPE_PROMPT_TEMPLATE = """\
The previous build failed repeatedly. Simplify the task to its minimal
working core so at least a basic version succeeds.

Original task:
{task}

Failure history:
{prior_summary}

Return a JSON object with keys:
  simplified_task (string),
  files (array of relative file paths),
  run_command (string),
  notes (string).
"""


class RetryEngine:
    """
    Adaptive retry orchestrator.

    Lifecycle per retry
    -------------------
    1. ``build_retry_context(...)`` — assemble situational context
    2. ``select_strategy(ctx)``     — choose optimal strategy
    3. ``execute(ctx, strategy)``   — produce retry patches (command / prompt)
    4. Caller runs the patched build iteration
    5. ``record_outcome(...)``       — persist result to reflection log

    The engine itself is stateless between calls; all history lives in the
    RetryContext passed by the caller.
    """

    def __init__(self) -> None:
        self._selector = StrategySelector()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def build_retry_context(
        self,
        *,
        task: str,
        job_id: str,
        failure_reason: Optional[str],
        exit_code: int,
        stderr: str,
        stdout: str,
        confidence: float,
        contradictions: List[str],
        warnings: List[str],
        attempt_number: int,
        prior_strategies: List[str],
        prior_failure_reasons: List[str],
        pattern_label: Optional[str] = None,
        recommended_strategy: Optional[str] = None,
    ) -> RetryContext:
        """Assemble a RetryContext from build-loop signals."""
        return RetryContext(
            task=task,
            job_id=job_id,
            failure_reason=failure_reason,
            exit_code=exit_code,
            stderr=stderr[:2000],   # guard against huge blobs
            stdout=stdout[:1000],
            confidence=confidence,
            contradictions=contradictions,
            warnings=warnings,
            attempt_number=attempt_number,
            prior_strategies=prior_strategies,
            prior_failure_reasons=prior_failure_reasons,
            pattern_label=pattern_label,
            recommended_strategy=recommended_strategy,
        )

    def select_strategy(self, ctx: RetryContext) -> RecoveryStrategy:
        """Select the optimal recovery strategy for this context."""
        return self._selector.select(ctx)

    def explain(self, ctx: RetryContext, strategy: RecoveryStrategy) -> str:
        """Return a human-readable explanation of the strategy choice."""
        return self._selector.explain(ctx, strategy)

    def execute(
        self,
        ctx: RetryContext,
        strategy: RecoveryStrategy,
        *,
        current_run_command: str = "",
        current_files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Produce the retry patch for a given strategy.

        Returns a dict with one or more of:
          - ``run_command``  : str — modified command for the next iteration
          - ``regen_prompt`` : str — LLM prompt to regenerate failing files
          - ``notes``        : str — human-readable explanation
          - ``strategy``     : str — strategy.value
          - ``skip``         : bool — True if no action possible (NONE / MANUAL)
        """
        patch: Dict[str, Any] = {
            "strategy": strategy.value,
            "notes": self.explain(ctx, strategy),
            "skip": False,
        }

        if strategy == RecoveryStrategy.NONE:
            patch["skip"] = True
            patch["notes"] = "No recovery action — proceeding with default build loop."
            return patch

        if strategy == RecoveryStrategy.MANUAL_INTERVENTION:
            patch["skip"] = True
            patch["notes"] = (
                "Manual intervention required. "
                f"Failure: {ctx.failure_reason or ctx.stderr_snippet[:120]}"
            )
            return patch

        if strategy == RecoveryStrategy.INSTALL_DEPS:
            base_cmd = current_run_command or f"{PY_BIN} -m pytest -q"
            # Strip any existing pip install prefix to avoid duplication
            if "pip install" in base_cmd:
                patch["run_command"] = base_cmd
            else:
                patch["run_command"] = (
                    f"{PY_BIN} -m pip install -q -r requirements.txt && {base_cmd}"
                )
            return patch

        if strategy == RecoveryStrategy.INCREASE_TIMEOUT:
            patch["run_command"] = current_run_command or f"{PY_BIN} -m pytest -q"
            patch["extended_timeout"] = True
            return patch

        if strategy == RecoveryStrategy.RETRY_WITH_FIXES:
            patch["run_command"] = current_run_command or f"{PY_BIN} -m pytest -q"
            return patch

        if strategy == RecoveryStrategy.SANDBOX_RETRY:
            patch["run_command"] = current_run_command or f"{PY_BIN} -m pytest -q"
            patch["force_new_sandbox"] = True
            return patch

        if strategy == RecoveryStrategy.LLM_REGEN:
            patch["regen_prompt"] = _REGEN_PROMPT_TEMPLATE.format(
                task=ctx.task,
                failure_reason=ctx.failure_reason or "Unknown",
                stderr_snippet=ctx.stderr_snippet,
                stdout_snippet=ctx.stdout_snippet,
                confidence=ctx.confidence,
                contradictions="\n".join(ctx.contradictions) if ctx.contradictions else "None",
                prior_summary=ctx.prior_summary(),
            )
            patch["run_command"] = current_run_command or f"{PY_BIN} -m pytest -q"
            return patch

        if strategy == RecoveryStrategy.REDUCE_SCOPE:
            patch["scope_reduction_prompt"] = _REDUCE_SCOPE_PROMPT_TEMPLATE.format(
                task=ctx.task,
                prior_summary=ctx.prior_summary(),
            )
            patch["run_command"] = current_run_command or f"{PY_BIN} -m pytest -q"
            return patch

        if strategy == RecoveryStrategy.ROLLBACK:
            patch["skip"] = True
            patch["notes"] = "Rollback requested — caller must restore last known-good workspace."
            patch["rollback"] = True
            return patch

        patch["skip"] = True
        return patch

    def record_outcome(
        self,
        ctx: RetryContext,
        strategy: RecoveryStrategy,
        *,
        succeeded: bool,
        new_exit_code: int,
        new_stdout: str = "",
        new_stderr: str = "",
        new_confidence: float = 0.0,
        reflection_engine: Any = None,
    ) -> RetryResult:
        """
        Persist the retry outcome and optionally update the reflection engine.

        Parameters
        ----------
        reflection_engine:
            Optional ReflectionEngine instance.  If provided and the retry
            succeeded, ``mark_recovery_outcome`` is called so future pattern
            recommendations improve.
        """
        result = RetryResult(
            job_id=ctx.job_id,
            attempt_number=ctx.attempt_number,
            strategy=strategy,
            succeeded=succeeded,
            new_exit_code=new_exit_code,
            new_stdout=new_stdout[:500],
            new_stderr=new_stderr[:500],
            new_confidence=new_confidence,
            notes=self.explain(ctx, strategy),
        )

        # Feed outcome back to reflection memory for future learning
        if reflection_engine is not None:
            try:
                # The reflection engine tracks recovery success per record_id.
                # We surface the strategy outcome through the session summary.
                reflection_engine.reflect(
                    build_result={
                        "task":       ctx.task,
                        "job_id":     ctx.job_id,
                        "exit_code":  new_exit_code,
                        "stdout":     new_stdout,
                        "stderr":     new_stderr,
                        "iterations": ctx.attempt_number,
                    },
                    epistemic_state={
                        "status":        "success" if succeeded else "epistemic_failure",
                        "confidence":    new_confidence,
                        "validated":     succeeded,
                        "contradictions": [],
                        "warnings":      [],
                        "raw_status":    "success" if succeeded else "error",
                    },
                )
            except Exception:
                pass

        return result
