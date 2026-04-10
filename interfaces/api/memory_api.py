from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List
import sys
import os
from pathlib import Path

# --- NVMe storage path enforcement ---
NVME_MEMORY_PATH = "/mnt/nvme/andie/memory/"
os.makedirs(NVME_MEMORY_PATH, exist_ok=True)

# --- Import memory modules (do not rewrite) ---
# Add andie/memory to sys.path for import
andie_memory_path = str(Path(__file__).resolve().parent.parent.parent / "andie" / "memory")
if andie_memory_path not in sys.path:
    sys.path.insert(0, andie_memory_path)


from andie.memory.memory_service import MemoryService

app = FastAPI()


class MemoryRequest(BaseModel):
    content: str
    metadata: Dict[str, Any] = {}


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    metadata: Dict[str, Any] = {}


memory = MemoryService()

@app.post("/memory/store")
def store_memory(req: MemoryRequest):
    try:
        return memory.store_memory(req.content, req.metadata)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/query")
def query_memory(req: QueryRequest):
    try:
        return memory.query_memory(req.query, top_k=req.top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Health check ---
@app.get("/health")
def health():
    return {"status": "ok"}
