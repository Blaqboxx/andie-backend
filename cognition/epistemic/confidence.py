"""
Confidence scoring for the epistemic subsystem.

ConfidenceEvaluator computes a weighted composite score from individual
ConfidenceFactors and attaches a ConfidenceAssessment to a Belief.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from .models import (
    Belief,
    BeliefSource,
    ConfidenceAssessment,
    ConfidenceFactors,
    ConfidenceLevel,
)

# ---------------------------------------------------------------------------
# Source reliability priors
# ---------------------------------------------------------------------------

_SOURCE_RELIABILITY: Dict[BeliefSource, float] = {
    BeliefSource.USER:         0.85,
    BeliefSource.MEMORY:       0.75,
    BeliefSource.LLM:          0.70,
    BeliefSource.EXTERNAL_API: 0.65,
    BeliefSource.RULE:         0.90,
    BeliefSource.SENSOR:       0.80,
    BeliefSource.INFERENCE:    0.60,
}

# Factor weights — must sum to 1.0
_WEIGHTS: Dict[str, float] = {
    "source_reliability":   0.30,
    "recency":              0.20,
    "corroboration":        0.25,
    "internal_consistency": 0.15,
    "domain_specificity":   0.10,
}

# Recency decay half-life in seconds (1 hour)
_HALF_LIFE_SECONDS: float = 3_600.0


def _recency_score(belief: Belief) -> float:
    """Exponential decay: 1.0 when fresh, approaching 0 as age grows."""
    import math
    age_s = (datetime.now(timezone.utc) - belief.updated_at.replace(tzinfo=timezone.utc)).total_seconds()
    age_s = max(age_s, 0.0)
    return math.exp(-0.693 * age_s / _HALF_LIFE_SECONDS)  # 0.693 ≈ ln(2)


def _level_from_score(score: float) -> ConfidenceLevel:
    if score >= 0.90:
        return ConfidenceLevel.CERTAIN
    if score >= 0.75:
        return ConfidenceLevel.HIGH
    if score >= 0.50:
        return ConfidenceLevel.MODERATE
    if score >= 0.25:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.UNKNOWN


class ConfidenceEvaluator:
    """
    Evaluates and updates confidence for a single belief.

    Usage::

        evaluator = ConfidenceEvaluator()
        assessment = evaluator.assess(belief, corroboration=0.8)
        # belief.confidence and belief.confidence_level are updated in-place
    """

    def assess(
        self,
        belief: Belief,
        *,
        corroboration: Optional[float] = None,
        internal_consistency: Optional[float] = None,
        domain_specificity: Optional[float] = None,
    ) -> ConfidenceAssessment:
        """
        Compute a composite confidence score and return a ConfidenceAssessment.

        Parameters
        ----------
        belief:
            The belief to evaluate.  Updated in-place (confidence, confidence_level).
        corroboration:
            Override for the corroboration factor (0–1).  If omitted, defaults
            to the belief's current confidence as a proxy.
        internal_consistency:
            Override for internal consistency (0–1).
        domain_specificity:
            Override for domain specificity (0–1).
        """
        src_score = _SOURCE_RELIABILITY.get(belief.source, 0.6)
        rec_score = _recency_score(belief)
        cor_score = corroboration if corroboration is not None else belief.confidence
        con_score = internal_consistency if internal_consistency is not None else 0.7
        dom_score = domain_specificity if domain_specificity is not None else 0.5

        factors = ConfidenceFactors(
            source_reliability=round(src_score, 4),
            recency=round(rec_score, 4),
            corroboration=round(cor_score, 4),
            internal_consistency=round(con_score, 4),
            domain_specificity=round(dom_score, 4),
        )

        composite = (
            _WEIGHTS["source_reliability"]   * factors.source_reliability
            + _WEIGHTS["recency"]              * factors.recency
            + _WEIGHTS["corroboration"]        * factors.corroboration
            + _WEIGHTS["internal_consistency"] * factors.internal_consistency
            + _WEIGHTS["domain_specificity"]   * factors.domain_specificity
        )
        composite = round(min(max(composite, 0.0), 1.0), 4)
        level = _level_from_score(composite)

        # Update belief in-place
        belief.confidence       = composite
        belief.confidence_level = level
        belief.updated_at       = datetime.utcnow()

        rationale = (
            f"src={factors.source_reliability:.2f} "
            f"rec={factors.recency:.2f} "
            f"cor={factors.corroboration:.2f} "
            f"con={factors.internal_consistency:.2f} "
            f"dom={factors.domain_specificity:.2f} "
            f"→ {composite:.4f} ({level.value})"
        )

        return ConfidenceAssessment(
            belief_id=belief.id,
            score=composite,
            level=level,
            factors=factors,
            rationale=rationale,
            assessed_at=datetime.utcnow(),
        )

    def batch_assess(self, beliefs: list[Belief], **kwargs) -> list[ConfidenceAssessment]:
        """Assess a list of beliefs, returning one assessment per belief."""
        return [self.assess(b, **kwargs) for b in beliefs]
