import json
import os

from .config import CHUNK_OVERLAP, CHUNK_SIZE, PROCESSED_PATH, RAW_PATH, ensure_brain_dirs
from .index import build_index, embed_texts
from .ingest_utils import chunk_text, read_txt, read_pdf


META_PATH = f"{PROCESSED_PATH}/metadata.json"
CHUNKS_PATH = f"{PROCESSED_PATH}/chunks.json"


def ingest_all() -> dict:
    ensure_brain_dirs()

    texts = []
    meta = []

    for root, _, files in os.walk(RAW_PATH):
        for filename in files:
            path = os.path.join(root, filename)
            
            # Handle TXT files
            if filename.lower().endswith(".txt"):
                content = read_txt(path)
                chunks = chunk_text(content, CHUNK_SIZE, CHUNK_OVERLAP)
                for index, chunk in enumerate(chunks):
                    texts.append(chunk)
                    meta.append({
                        "source": path,
                        "chunk_id": index,
                    })
            
            # Handle PDF files
            elif filename.lower().endswith(".pdf"):
                try:
                    content, page_map = read_pdf(path)
                    chunks = chunk_text(content, CHUNK_SIZE, CHUNK_OVERLAP)
                    for index, chunk in enumerate(chunks):
                        texts.append(chunk)
                        # Extract page number from chunk context
                        # Count PAGE BREAKs in chunk to estimate which page we're on
                        page_count = chunk.count("---PAGE BREAK---")
                        estimated_page = page_map[0] + page_count if page_map else 1
                        meta.append({
                            "source": path,
                            "chunk_id": index,
                            "file_type": "pdf",
                            "page": estimated_page,
                        })
                except Exception as e:
                    print(f"Warning: Failed to ingest PDF {path}: {e}")
                    continue

    os.makedirs(PROCESSED_PATH, exist_ok=True)
    with open(META_PATH, "w", encoding="utf-8") as fp:
        json.dump(meta, fp, indent=2)

    with open(CHUNKS_PATH, "w", encoding="utf-8") as fp:
        json.dump(texts, fp, ensure_ascii=False)

    if texts:
        embeddings = embed_texts(texts)
        build_index(embeddings)

    return {"chunks": len(texts), "sources": len({item['source'] for item in meta})}
