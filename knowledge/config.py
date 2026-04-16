import os
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_FALLBACK = _PROJECT_ROOT / "storage" / "andie-brain"
_DEFAULT_BASE = "/mnt/andie-brain" if Path("/mnt/andie-brain").exists() else str(_LOCAL_FALLBACK)

BASE_PATH = os.environ.get("ANDIE_BRAIN_BASE_PATH", _DEFAULT_BASE)

RAW_PATH = f"{BASE_PATH}/knowledge/raw"
PROCESSED_PATH = f"{BASE_PATH}/knowledge/processed"
INDEX_PATH = f"{BASE_PATH}/vector_index/faiss"
EMBED_PATH = f"{BASE_PATH}/embeddings"
SKILLS_PATH = f"{BASE_PATH}/skills"
CACHE_PATH = f"{BASE_PATH}/cache"
MODELS_PATH = f"{BASE_PATH}/models"
LOGS_PATH = f"{BASE_PATH}/logs"

EMBED_MODEL = os.environ.get("ANDIE_EMBED_MODEL", "all-MiniLM-L6-v2")
CHUNK_SIZE = int(os.environ.get("ANDIE_KNOWLEDGE_CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.environ.get("ANDIE_KNOWLEDGE_CHUNK_OVERLAP", "100"))
TOP_K = int(os.environ.get("ANDIE_KNOWLEDGE_TOP_K", "5"))


def ensure_brain_dirs() -> None:
    for path in (
        RAW_PATH,
        PROCESSED_PATH,
        INDEX_PATH,
        EMBED_PATH,
        SKILLS_PATH,
        CACHE_PATH,
        MODELS_PATH,
        LOGS_PATH,
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
