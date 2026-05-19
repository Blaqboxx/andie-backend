"""Probe: GPU node (Blaqtower3 / Ollama) status."""
from __future__ import annotations
import httpx

OLLAMA_BASE = "http://192.168.50.9:11434"


async def run() -> list[dict]:
    checks = []

    # 1. Ollama reachability
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
        data = r.json()
        models = [m["name"] for m in data.get("models", [])]
        checks.append({
            "check": "ollama-api",
            "status": "healthy",
            "detail": f"{len(models)} models loaded: {', '.join(models[:5])}",
        })
    except Exception as e:
        checks.append({"check": "ollama-api", "status": "unreachable", "detail": str(e)[:120]})
        return checks

    # 2. Running models (warm check)
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/ps")
        ps = r.json()
        running = ps.get("models", [])
        if running:
            details = []
            for m in running:
                vram = m.get("size_vram", 0)
                details.append(f"{m['name']} ({round(vram/1e9, 1)}GB VRAM)")
            checks.append({
                "check": "gpu-warm-models",
                "status": "healthy",
                "detail": "warm: " + ", ".join(details),
            })
        else:
            checks.append({
                "check": "gpu-warm-models",
                "status": "degraded",
                "detail": "no models currently loaded (cold start expected ~40s)",
            })
    except Exception as e:
        checks.append({"check": "gpu-warm-models", "status": "unknown", "detail": str(e)[:120]})

    return checks
