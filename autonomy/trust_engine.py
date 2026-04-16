from __future__ import annotations

from autonomy.learning_engine import memory, score_skill


def compute_trust(skill_name: str, context_key: str | None = None, replaced_from: str | None = None) -> float:
    """Compute operator trust for a skill in [0.0, 1.0].

    Trust starts from the execution-derived score and is dampened by
    accumulated operator-friction signals (swaps_from + skips).

    Friction is capped at 0.5 so trust can never drop below 50% of the
    execution-derived base score — preventing overfitting to a single
    incident session.
    """
    ck = memory._canonicalize_context_key(context_key)
    key = f"{skill_name}::{ck}" if ck else str(skill_name or "").strip()
    data = memory.data.get(key) or memory.data.get(str(skill_name or "").strip()) or {}

    fb = data.get("operator_feedback") or {}
    swaps_from = int(fb.get("swaps_from", 0) or 0)
    skips = int(fb.get("skips", 0) or 0)

    # Each swap-from contributes 10 %; each skip contributes 5 %.
    # Friction is capped so sustained negative feedback can't zero a skill out.
    friction = min((swaps_from * 0.1) + (skips * 0.05), 0.5)

    base = score_skill(skill_name, context_key=context_key, replaced_from=replaced_from)
    trust = base * (1.0 - friction)
    return round(max(0.0, min(trust, 1.0)), 3)
