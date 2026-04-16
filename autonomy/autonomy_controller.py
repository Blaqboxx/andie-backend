from __future__ import annotations

from typing import Any, Dict

from autonomy.autonomy_profiles import DEFAULT_PROFILE, PROFILES
from autonomy.trust_engine import compute_trust

# Defaults are aligned to the balanced profile for backward compatibility.
AUTO_THRESHOLD: float = PROFILES[DEFAULT_PROFILE]["auto_threshold"]
REVIEW_THRESHOLD: float = PROFILES[DEFAULT_PROFILE]["review_threshold"]

# High-risk skills require elevated trust even in auto global-mode.
HIGH_RISK_AUTO_THRESHOLD: float = 0.85


def decide_execution_mode(
    step: Dict[str, Any],
    context_key: str | None = None,
    global_mode: str = "assisted",
    profile: str = DEFAULT_PROFILE,
) -> str:
    """Return the execution mode for a single plan step.

    Returns one of:
        "auto"     — safe to execute without operator review
        "approval" — operator must confirm before execution
        "block"    — skill trust is too low; should not execute

    Args:
        step        Plan step dict with at least a "step" (skill name) key.
                    Optional "risk" key ("high" | "medium" | "low") is used
                    for the high-risk guardrail.
        context_key Contextual key qualifying the skill (e.g. stream type).
        global_mode One of "assisted", "manual", "auto", or "incident".
                    Defaults to "assisted" (adaptive trust-based policy).
    """
    skill_name = str(step.get("step") or "").strip()
    if not skill_name:
        return "block"

    risk = str(step.get("risk") or "").strip().lower()
    profile_name = str(profile or "").strip().lower() or DEFAULT_PROFILE
    thresholds = PROFILES.get(profile_name, PROFILES[DEFAULT_PROFILE])

    # ---------------------------------------------------------------------------
    # Fixed global-mode overrides
    # ---------------------------------------------------------------------------
    if global_mode == "incident":
        # Incident mode bypasses all gates — operator has taken explicit control.
        return "auto"

    if global_mode == "manual":
        return "approval"

    if global_mode == "auto":
        # High-risk skills still require elevated trust even in auto mode.
        if risk == "high":
            trust = compute_trust(skill_name, context_key)
            if trust < HIGH_RISK_AUTO_THRESHOLD:
                return "approval"
        return "auto"

    # ---------------------------------------------------------------------------
    # Assisted mode — adaptive, trust-based policy (default)
    # ---------------------------------------------------------------------------
    trust = compute_trust(skill_name, context_key)

    # Safety guardrail: high-risk skills need elevated trust regardless of mode.
    if risk == "high" and trust < HIGH_RISK_AUTO_THRESHOLD:
        return "approval"

    if trust >= thresholds["auto_threshold"]:
        return "auto"
    if trust >= thresholds["review_threshold"]:
        return "approval"
    if trust < thresholds["block_threshold"]:
        return "block"
    return "approval"
