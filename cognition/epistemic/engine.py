"""
Epistemic Engine — orchestrates belief management for the ANDIE cognition layer.

EpistemicEngine is the single entry point for:
  - adding / retracting beliefs
  - refreshing confidence scores
  - scanning for contradictions
  - producing a full EpistemicState snapshot
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .confidence import ConfidenceEvaluator
from .contradictions import ContradictionDetector
from .models import (
    Belief,
    BeliefSource,
    BeliefStatus,
    ContradictionSeverity,
    EpistemicState,
)


class EpistemicEngine:
    """
    Stateful belief management engine.

    Usage::

        engine = EpistemicEngine(agent_id="andie")
        belief = engine.add_belief("The system is healthy.", source=BeliefSource.SENSOR)
        state  = engine.snapshot()
    """

    def __init__(self, agent_id: str = "andie") -> None:
        self.agent_id = agent_id
        self._beliefs: Dict[str, Belief] = {}
        self._evaluator = ConfidenceEvaluator()
        self._detector  = ContradictionDetector()

    # ------------------------------------------------------------------ #
    # Belief lifecycle                                                     #
    # ------------------------------------------------------------------ #

    def add_belief(
        self,
        claim: str,
        source: BeliefSource = BeliefSource.INFERENCE,
        confidence: float = 0.5,
        context: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Belief:
        """
        Create, score, and store a new belief.

        If the same claim (case-insensitive) already exists and is active,
        the existing belief's confidence is updated instead.
        """
        existing = self._find_by_claim(claim)
        if existing is not None and existing.status == BeliefStatus.ACTIVE:
            existing.confidence = max(existing.confidence, confidence)
            self._evaluator.assess(existing)
            return existing

        belief = Belief(
            claim=claim,
            source=source,
            confidence=confidence,
            context=context or {},
            tags=tags or [],
        )
        self._evaluator.assess(belief)
        self._beliefs[belief.id] = belief
        return belief

    def retract_belief(self, belief_id: str, reason: str = "") -> bool:
        """Mark a belief as RETRACTED. Returns True if found."""
        belief = self._beliefs.get(belief_id)
        if belief is None:
            return False
        belief.status = BeliefStatus.RETRACTED
        belief.updated_at = datetime.utcnow()
        if reason:
            belief.context["retraction_reason"] = reason
        return True

    def supersede_belief(self, old_id: str, new_claim: str, **kwargs) -> Belief:
        """
        Retract an existing belief and replace it with a new one.

        Returns the new Belief.
        """
        old = self._beliefs.get(old_id)
        if old is not None:
            old.status = BeliefStatus.SUPERSEDED
            old.updated_at = datetime.utcnow()
        new = self.add_belief(new_claim, **kwargs)
        if old is not None:
            old.superseded_by = new.id
        return new

    # ------------------------------------------------------------------ #
    # Refresh & scanning                                                   #
    # ------------------------------------------------------------------ #

    def refresh_confidence(self) -> None:
        """Re-evaluate confidence for all active beliefs (e.g. due to age decay)."""
        for belief in self._beliefs.values():
            if belief.status == BeliefStatus.ACTIVE:
                self._evaluator.assess(belief)

    def scan_contradictions(self, auto_resolve_tolerables: bool = True) -> int:
        """
        Detect contradictions and optionally auto-resolve tolerable ones.

        Returns the number of unresolved contradictions.
        """
        report = self._detector.scan(list(self._beliefs.values()))
        unresolved = 0
        for contradiction in report.contradictions:
            if auto_resolve_tolerables and contradiction.severity == ContradictionSeverity.TOLERABLE:
                self._detector.resolve(contradiction, "auto-resolved: tolerable uncertainty")
            else:
                unresolved += 1
        return unresolved

    # ------------------------------------------------------------------ #
    # Snapshot                                                             #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> EpistemicState:
        """Produce a full EpistemicState reflecting current belief set."""
        all_beliefs = list(self._beliefs.values())
        assessments = [
            self._evaluator.assess(b)
            for b in all_beliefs
            if b.status == BeliefStatus.ACTIVE
        ]
        contradiction_report = self._detector.scan(all_beliefs)

        return EpistemicState(
            agent_id=self.agent_id,
            beliefs=all_beliefs,
            confidence_assessments=assessments,
            contradiction_report=contradiction_report,
            snapshot_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _find_by_claim(self, claim: str) -> Optional[Belief]:
        lower = claim.lower().strip()
        for b in self._beliefs.values():
            if b.claim.lower().strip() == lower:
                return b
        return None

    # ------------------------------------------------------------------ #
    # Build artifact evaluation                                           #
    # ------------------------------------------------------------------ #

    def evaluate(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        """
        Epistemically evaluate a build artifact before accepting its status.

        ``artifact`` must contain at minimum:
            - ``status``   (str)  — claimed outcome ("success", "error", …)
            - ``exit_code`` (int) — process return code
            - ``stdout``   (str)  — captured stdout
            - ``stderr``   (str)  — captured stderr

        Optional keys: ``code`` (str), ``iterations`` (int), ``brief`` (str).

        Returns a dict::

            {
                "status":         str,   # epistemic verdict
                "confidence":     float, # overall confidence [0, 1]
                "validated":      bool,
                "contradictions": list[str],
                "warnings":       list[str],
                "raw_status":     str,   # original claimed status
            }
        """
        from .validators.output_validator import OutputValidator
        from .validators.syntax_validator import SyntaxValidator

        claimed_status: str = str(artifact.get("status", "unknown"))
        exit_code: int = int(artifact.get("exit_code", -1))
        stdout: str   = str(artifact.get("stdout", ""))
        stderr: str   = str(artifact.get("stderr", ""))
        code: str     = str(artifact.get("code", ""))
        iterations: int = int(artifact.get("iterations", 1))

        contradictions: list[str] = []
        warnings:       list[str] = []

        # ── Belief 1: claimed outcome ──────────────────────────────────
        b_claimed = self.add_belief(
            f"Build claimed status: {claimed_status}.",
            source=BeliefSource.RULE,
            confidence=0.6,
            tags=["build", "status"],
        )

        # ── Belief 2: exit code evidence ──────────────────────────────
        exit_success = exit_code == 0
        b_exit = self.add_belief(
            f"Process exited with code {exit_code} ({'success' if exit_success else 'failure'}).",
            source=BeliefSource.SENSOR,
            confidence=0.95,
            tags=["build", "exit_code"],
        )

        # ── Belief 3: stderr evidence ──────────────────────────────────
        stderr_clean = not stderr.strip()
        b_stderr = self.add_belief(
            "Stderr is empty." if stderr_clean else f"Stderr contains output: {stderr[:120]}",
            source=BeliefSource.SENSOR,
            confidence=0.90,
            tags=["build", "stderr"],
        )

        # ── Contradiction check: success claimed but exit_code != 0 ───
        if claimed_status == "success" and not exit_success:
            contradictions.append(
                f"Build marked 'success' but exit_code={exit_code}."
            )

        # ── Contradiction check: success claimed but stderr present ───
        error_keywords = ("error", "exception", "traceback", "failed", "fatal")
        stderr_has_error = any(kw in stderr.lower() for kw in error_keywords)
        if claimed_status == "success" and stderr_has_error:
            contradictions.append(
                "Build marked 'success' but stderr contains error indicators."
            )

        # ── LLM failure comment in generated code ─────────────────────
        if code and ("# LLM error" in code or "# LLM unavailable" in code):
            contradictions.append("Generated code contains LLM failure comment.")
            warnings.append("Code may be a stub produced by a failed LLM call.")

        # ── Output validation on stdout ───────────────────────────────
        ov = OutputValidator()
        ov_result = ov.validate(stdout, target_id="build_stdout")
        if not ov_result.passed:
            for msg in ov_result.messages:
                warnings.append(f"OutputValidator: {msg}")

        # ── Syntax validation on generated code ───────────────────────
        if code:
            sv = SyntaxValidator()
            sv_result = sv.validate(code, target_id="build_code")
            if not sv_result.passed:
                for msg in sv_result.messages:
                    warnings.append(f"SyntaxValidator: {msg}")

        # ── Iteration penalty ─────────────────────────────────────────
        if iterations > 3:
            warnings.append(
                f"Build required {iterations} iterations — confidence reduced."
            )

        # ── Refresh confidence & scan contradictions ───────────────────
        self.refresh_confidence()
        self.scan_contradictions()
        state = self.snapshot()

        overall_conf = state.overall_confidence()

        # ── Epistemic verdict ─────────────────────────────────────────
        # Exit code is authoritative — a non-zero exit can never be "success"
        # regardless of validator warnings.
        if not exit_success:
            epistemic_status = "epistemic_failure"
        elif contradictions:
            if overall_conf >= 0.6:
                epistemic_status = "partial_success"
            else:
                epistemic_status = "epistemic_failure"
        elif warnings:
            epistemic_status = "success_with_warnings"
        else:
            epistemic_status = "success" if claimed_status == "success" else claimed_status

        # Iteration penalty caps confidence
        if iterations > 3:
            overall_conf = round(overall_conf * (1.0 - 0.05 * (iterations - 3)), 4)
            overall_conf = max(overall_conf, 0.0)

        return {
            "status":         epistemic_status,
            "confidence":     round(overall_conf, 4),
            "validated":      not contradictions,
            "contradictions": contradictions,
            "warnings":       warnings,
            "raw_status":     claimed_status,
        }

    # ------------------------------------------------------------------ #

    @property
    def active_count(self) -> int:
        return sum(1 for b in self._beliefs.values() if b.status == BeliefStatus.ACTIVE)
