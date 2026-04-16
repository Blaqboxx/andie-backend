import os

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from .config import EMBED_MODEL, INDEX_PATH


os.makedirs(INDEX_PATH, exist_ok=True)
_MODEL = None


def get_model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer(EMBED_MODEL)
    return _MODEL


def build_index(embeddings: np.ndarray):
    vectors = np.asarray(embeddings, dtype="float32")
    dim = vectors.shape[1]
    index = faiss.IndexFlatL2(dim)
    index.add(vectors)
    faiss.write_index(index, f"{INDEX_PATH}/index.faiss")


def load_index():
    return faiss.read_index(f"{INDEX_PATH}/index.faiss")


def embed_texts(texts):
    model = get_model()
    vectors = model.encode(texts, show_progress_bar=True, normalize_embeddings=True)
    return np.asarray(vectors, dtype="float32")
