"""
Contradiction detection and resolution for the epistemic subsystem.

ContradictionDetector scans a set of Beliefs for logical or semantic
conflicts and produces a ContradictionReport.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

from .models import (
    Belief,
    BeliefStatus,
    Contradiction,
    ContradictionReport,
    ContradictionSeverity,
)

# ---------------------------------------------------------------------------
# Heuristic negation pairs
# ---------------------------------------------------------------------------

_NEGATION_PREFIXES = ("not ", "no ", "never ", "cannot ", "isn't ", "aren't ", "won't ")
_ANTONYM_PAIRS: List[Tuple[str, str]] = [
    ("online", "offline"),
    ("active", "inactive"),
    ("healthy", "unhealthy"),
    ("up", "down"),
    ("connected", "disconnected"),
    ("true", "false"),
    ("enabled", "disabled"),
    ("success", "failure"),
    ("secure", "vulnerable"),
    ("available", "unavailable"),
]


def _has_negation(claim: str, keyword: str) -> bool:
    """Return True if `claim` contains a negated form of `keyword`."""
    lower = claim.lower()
    for prefix in _NEGATION_PREFIXES:
        if f"{prefix}{keyword}" in lower:
            return True
    return False


def _antonym_conflict(a: str, b: str) -> bool:
    """Return True if the two claims express known antonym pairs."""
    la, lb = a.lower(), b.lower()
    for word1, word2 in _ANTONYM_PAIRS:
        if word1 in la and word2 in lb:
            return True
        if word2 in la and word1 in lb:
            return True
    return False


def _direct_negation(a: str, b: str) -> bool:
    """Return True if one claim is a simple negation of the other."""
    la, lb = a.lower().strip(), b.lower().strip()
    for prefix in _NEGATION_PREFIXES:
        candidate = prefix + la
        if candidate == lb:
            return True
        candidate = prefix + lb
        if candidate == la:
            return True
    # "not X" vs "X" pattern with shared tokens
    tokens_a = set(la.split())
    tokens_b = set(lb.split())
    shared = tokens_a & tokens_b
    if len(shared) >= max(1, min(len(tokens_a), len(tokens_b)) // 2):
        neg_a = any(t in tokens_a for t in ("not", "no", "never", "cannot"))
        neg_b = any(t in tokens_b for t in ("not", "no", "never", "cannot"))
        if neg_a != neg_b:  # exactly one is negated
            return True
    return False


def _score_severity(a: Belief, b: Belief, conflict_type: str) -> ContradictionSeverity:
    confidence_product = a.confidence * b.confidence
    if conflict_type == "direct_negation":
        if confidence_product >= 0.64:  # both high
            return ContradictionSeverity.CRITICAL
        return ContradictionSeverity.MAJOR
    if conflict_type == "antonym":
        if confidence_product >= 0.49:
            return ContradictionSeverity.MAJOR
        return ContradictionSeverity.MINOR
    return ContradictionSeverity.TOLERABLE


class ContradictionDetector:
    """
    Detects contradictions between beliefs using heuristic rules.

    For production quality, replace or augment ``_are_contradictory()``
    with an LLM-based semantic similarity check.

    Usage::

        detector = ContradictionDetector()
        report = detector.scan(beliefs)
    """

    def scan(self, beliefs: List[Belief]) -> ContradictionReport:
        """
        Compare every active belief pair and collect contradictions.

        Returns a ContradictionReport with all detected conflicts.
        """
        active = [b for b in beliefs if b.status == BeliefStatus.ACTIVE]
        contradictions: List[Contradiction] = []
        seen: set[frozenset] = set()

        for i, ba in enumerate(active):
            for ba2 in active[i + 1:]:
                pair_key = frozenset({ba.id, ba2.id})
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                conflict_type = self._are_contradictory(ba, ba2)
                if conflict_type is None:
                    continue

                severity = _score_severity(ba, ba2, conflict_type)
                contradictions.append(
                    Contradiction(
                        belief_a_id=ba.id,
                        belief_b_id=ba2.id,
                        severity=severity,
                        description=(
                            f"[{conflict_type}] '{ba.claim[:60]}' ↔ '{ba2.claim[:60]}'"
                        ),
                        detected_at=datetime.utcnow(),
                    )
                )

        return ContradictionReport(
            belief_ids_scanned=[b.id for b in active],
            contradictions=contradictions,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _are_contradictory(self, a: Belief, b: Belief) -> str | None:
        """
        Return the conflict type string if the two beliefs contradict,
        or None if they are compatible.
        """
        if _direct_negation(a.claim, b.claim):
            return "direct_negation"
        if _antonym_conflict(a.claim, b.claim):
            return "antonym"
        return None

    def resolve(
        self,
        contradiction: Contradiction,
        resolution_note: str,
        losing_belief: Belief | None = None,
    ) -> None:
        """
        Mark a contradiction as resolved.

        Optionally retract the losing belief.
        """
        contradiction.resolved = True
        contradiction.resolution = resolution_note
        contradiction.resolved_at = datetime.utcnow()
        if losing_belief is not None:
            losing_belief.status = BeliefStatus.RETRACTED
            losing_belief.updated_at = datetime.utcnow()
