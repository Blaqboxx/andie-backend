"""Probe: model availability and inference readiness."""
from __future__ import annotations
import httpx
import time

OLLAMA_BASE = "http://192.168.50.9:11434"
REQUIRED_MODELS = ["mistral:latest", "nomic-embed-text:latest"]


async def run() -> list[dict]:
    checks = []

    # 1. Check each required model exists
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
        available = {m["name"] for m in r.json().get("models", [])}
    except Exception as e:
        return [{"check": "model-registry", "status": "unreachable", "detail": str(e)[:120]}]

    for model in REQUIRED_MODELS:
        if model in available:
            checks.append({"check": f"model:{model}", "status": "healthy", "detail": "available"})
        else:
            checks.append({"check": f"model:{model}", "status": "failed", "detail": "not found in registry"})

    # 2. Inference latency probe (lightweight — embed a short string)
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{OLLAMA_BASE}/api/embeddings", json={
                "model": "nomic-embed-text:latest",
                "prompt": "ping",
            })
        latency = round((time.monotonic() - t0) * 1000)
        if r.status_code == 200:
            status = "healthy" if latency < 5000 else "degraded"
            checks.append({
                "check": "inference-latency",
                "status": status,
                "detail": f"embed probe {latency}ms {'(cold start)' if latency > 10000 else ''}",
            })
        else:
            checks.append({"check": "inference-latency", "status": "degraded", "detail": f"HTTP {r.status_code}"})
    except Exception as e:
        checks.append({"check": "inference-latency", "status": "degraded", "detail": str(e)[:120]})

    return checks
