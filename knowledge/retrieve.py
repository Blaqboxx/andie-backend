import json
import os
from typing import Any, Dict, List

import numpy as np

from .config import PROCESSED_PATH, TOP_K
from .index import embed_texts, load_index


CHUNKS_PATH = f"{PROCESSED_PATH}/chunks.json"
META_PATH = f"{PROCESSED_PATH}/metadata.json"


def _load_chunks_and_meta() -> tuple[List[str], List[Dict[str, Any]]]:
    if not os.path.exists(CHUNKS_PATH) or not os.path.exists(META_PATH):
        return [], []

    with open(CHUNKS_PATH, encoding="utf-8") as f:
        chunks = json.load(f)
    with open(META_PATH, encoding="utf-8") as f:
        meta = json.load(f)

    return chunks, meta


def search(query: str, k: int = TOP_K):
    chunks, meta = _load_chunks_and_meta()
    if not chunks:
        return []

    index = load_index()
    q_emb = embed_texts([query])
    distances, indices = index.search(np.asarray(q_emb, dtype="float32"), max(1, min(k, len(chunks))))

    results = []
    for pos, idx in enumerate(indices[0]):
        if idx < 0 or idx >= len(chunks):
            continue
        results.append(
            {
                "text": chunks[idx],
                "meta": meta[idx] if idx < len(meta) else {},
                "distance": float(distances[0][pos]),
            }
        )
    return results
