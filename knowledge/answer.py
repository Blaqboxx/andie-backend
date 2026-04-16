import re
import os
from typing import Any, Dict, List

from .config import TOP_K
from .retrieve import search


MAX_DISTANCE = float(os.environ.get("ANDIE_KNOWLEDGE_MAX_DISTANCE", "1.25"))


def _split_sentences(text: str) -> List[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    return [part.strip() for part in parts if part.strip()]


def _query_terms(query: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]+", query.lower()) if len(token) > 2}


def _score_sentence(sentence: str, terms: set[str]) -> int:
    sentence_terms = set(re.findall(r"[a-z0-9_]+", sentence.lower()))
    return len(sentence_terms & terms)


def _top_sentences(results: List[Dict[str, Any]], query: str, limit: int = 4) -> List[str]:
    terms = _query_terms(query)
    candidates: List[tuple[int, int, str]] = []
    seen = set()
    for result_index, item in enumerate(results):
        for sentence in _split_sentences(item.get("text") or ""):
            cleaned = sentence.strip()
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            candidates.append((_score_sentence(cleaned, terms), -result_index, cleaned))

    ranked = sorted(candidates, key=lambda entry: (entry[0], entry[1], len(entry[2])), reverse=True)
    selected = [sentence for score, _, sentence in ranked if score > 0][:limit]
    if selected:
        return selected

    fallback = []
    for item in results[:limit]:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if text:
            fallback.append(text[:220])
    return fallback[:limit]


def answer_with_knowledge(query: str, mode: str = "answer", k: int = TOP_K) -> Dict[str, Any]:
    results = search(query, k=k)
    best_distance = float(results[0].get("distance", 9999.0)) if results else 9999.0
    if not results or best_distance > MAX_DISTANCE:
        return {
            "status": "no_results",
            "mode": mode,
            "answer": "No relevant knowledge found.",
            "results": [],
            "sources": [],
        }

    sentences = _top_sentences(results, query)
    sources = []
    for item in results:
        source = (item.get("meta") or {}).get("source")
        if source and source not in sources:
            sources.append(source)

    if mode == "summarize":
        answer = "Summary from local knowledge:\n" + "\n".join(f"- {sentence}" for sentence in sentences[:4])
    elif mode == "explain":
        answer = "Explanation from local knowledge:\n" + "\n".join(f"- {sentence}" for sentence in sentences[:4])
    else:
        lead = sentences[0] if sentences else "Relevant local knowledge was found."
        supporting = sentences[1:3]
        answer = lead
        if supporting:
            answer += "\n\nSupporting points:\n" + "\n".join(f"- {sentence}" for sentence in supporting)

    return {
        "status": "ok",
        "mode": mode,
        "answer": answer,
        "results": results,
        "sources": sources,
    }