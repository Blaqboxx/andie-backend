"""Probe: task worker queue depth and pipeline health."""
from __future__ import annotations
import asyncio

# Backend-internal task store (imported lazily to avoid circular imports)

async def run() -> list[dict]:
    checks = []

    # 1. Redis availability (try to ping)
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url("redis://localhost:6379", socket_connect_timeout=2)
        await asyncio.wait_for(client.ping(), timeout=2.0)
        await client.aclose()

        # Queue depth from celery / task lists
        client2 = aioredis.from_url("redis://localhost:6379", socket_connect_timeout=2)
        try:
            queues = ["celery", "andie:tasks", "default"]
            depths = {}
            for q in queues:
                length = await asyncio.wait_for(client2.llen(q), timeout=1.0)
                if length:
                    depths[q] = length
            await client2.aclose()

            if depths:
                detail = "queues: " + ", ".join(f"{q}={n}" for q, n in depths.items())
                status = "degraded" if any(n > 100 for n in depths.values()) else "healthy"
            else:
                detail = "Redis reachable — queues empty"
                status = "healthy"

            checks.append({"check": "redis-queue", "status": status, "detail": detail})
        except Exception as e:
            await client2.aclose()
            checks.append({"check": "redis-queue", "status": "unknown", "detail": str(e)[:120]})

    except Exception as e:
        checks.append({
            "check": "redis-queue",
            "status": "unreachable",
            "detail": f"Redis not reachable: {str(e)[:80]}",
        })

    # 2. In-process task store (andie internal)
    try:
        from andie_backend.andie.core.task_store import get_all_tasks
        tasks = get_all_tasks()
        pending = [t for t in tasks if getattr(t, "status", None) in ("pending", "running")]
        stalled = [t for t in tasks if getattr(t, "status", None) == "stalled"]
        checks.append({
            "check": "task-queue",
            "status": "degraded" if stalled else "healthy",
            "detail": f"{len(pending)} active, {len(stalled)} stalled, {len(tasks)} total",
        })
    except ImportError:
        checks.append({"check": "task-queue", "status": "unknown", "detail": "task_store not available"})
    except Exception as e:
        checks.append({"check": "task-queue", "status": "unknown", "detail": str(e)[:120]})

    return checks
