from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import json
import os

from knowledge.config import PROCESSED_PATH, RAW_PATH, SKILLS_PATH

router = APIRouter()


class Query(BaseModel):
    query: str
    k: int | None = None


class KnowledgeAnswerRequest(BaseModel):
    query: str
    mode: str = "answer"
    k: int | None = None


@router.post("/knowledge/search")
def knowledge_search(q: Query):
    from knowledge.retrieve import search

    results = search(q.query, k=q.k) if q.k else search(q.query)
    return {"results": results}


@router.post("/knowledge/answer")
def knowledge_answer(q: KnowledgeAnswerRequest):
    from knowledge.answer import answer_with_knowledge

    return answer_with_knowledge(q.query, mode=q.mode, k=q.k or 5)


@router.post("/knowledge/ingest")
def knowledge_ingest():
    try:
        from knowledge.ingest import ingest_all

        return {"status": "ok", **ingest_all()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/knowledge/catalog")
def knowledge_catalog(limit: int = 50):
    metadata_path = f"{PROCESSED_PATH}/metadata.json"
    chunks_path = f"{PROCESSED_PATH}/chunks.json"

    metadata = []
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, encoding="utf-8") as fp:
                metadata = json.load(fp)
        except Exception:
            metadata = []

    total_chunks = 0
    if os.path.exists(chunks_path):
        try:
            with open(chunks_path, encoding="utf-8") as fp:
                total_chunks = len(json.load(fp))
        except Exception:
            total_chunks = 0

    source_counts = {}
    for item in metadata:
        source = str((item or {}).get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

    sources = [
        {
            "source": source,
            "chunks": count,
        }
        for source, count in sorted(source_counts.items(), key=lambda entry: entry[1], reverse=True)
    ]

    raw_files = []
    if os.path.isdir(RAW_PATH):
        for root, _, files in os.walk(RAW_PATH):
            for filename in files:
                raw_files.append(os.path.join(root, filename))

    skills = []
    if os.path.isdir(SKILLS_PATH):
        for filename in sorted(os.listdir(SKILLS_PATH)):
            if filename.endswith(".json"):
                skills.append(filename[:-5])

    return {
        "summary": {
            "totalSources": len(source_counts),
            "totalChunks": total_chunks,
            "rawFiles": len(raw_files),
            "skills": len(skills),
        },
        "sources": sources[: max(1, limit)],
        "skills": skills,
        "paths": {
            "raw": RAW_PATH,
            "processed": PROCESSED_PATH,
            "skills": SKILLS_PATH,
        },
    }
