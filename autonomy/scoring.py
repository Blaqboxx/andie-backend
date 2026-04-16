from __future__ import annotations


SOURCE_WEIGHTS = {
    "local": 1.0,
    "skills": 0.9,
    "openai": 0.7,
    "web": 0.5,
}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def compute_confidence(
    similarity: float = 0.0,
    source_weight: float = 0.0,
    success_rate: float = 0.0,
) -> float:
    score = 0.4 * clamp(similarity) + 0.3 * clamp(source_weight) + 0.3 * clamp(success_rate)
    if success_rate > 0.7:
        score += 0.1
    return round(clamp(score), 3)


def compute_trust(source: str, recency_score: float = 0.5) -> float:
    return round(clamp(SOURCE_WEIGHTS.get(source, 0.5) * clamp(recency_score)), 3)
