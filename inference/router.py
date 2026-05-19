"""
ANDIE Resilient Inference Router
Prioritises OLLAMA_BASE_URL, probes health before use, fails over to fallbacks.
"""
import os
import time
import logging
import httpx

logger = logging.getLogger("andie.inference")

_FALLBACK_NODES = [
    "http://host.docker.internal:11434",
]

TIMEOUTS = httpx.Timeout(connect=5.0, read=300.0, write=5.0, pool=10.0)
PROBE_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0)


def _node_list():
    primary = os.environ.get("OLLAMA_BASE_URL", "").strip().rstrip("/")
    nodes = ([primary] if primary else []) + [
        n.rstrip("/") for n in _FALLBACK_NODES
        if n.strip().rstrip("/") != primary
    ]
    return [n for n in nodes if n]


async def _probe(client, host):
    try:
        r = await client.get(f"{host}/api/tags", timeout=PROBE_TIMEOUT)
        return r.status_code == 200
    except Exception as exc:
        logger.debug("Probe failed %s: %s", host, exc)
        return False


async def chat(messages, model=None, system=None):
    """
    Send a chat request to the first healthy Ollama node.
    Returns {"response": str, "model": str, "node": str, "latency_ms": int}
    Raises RuntimeError if all nodes are unavailable.
    """
    effective_model = model or os.environ.get("OLLAMA_MODEL", "phi3:mini")
    if system:
        messages = [{"role": "system", "content": system}] + list(messages)

    nodes = _node_list()
    last_error = None

    async with httpx.AsyncClient(timeout=TIMEOUTS) as client:
        for node in nodes:
            if not await _probe(client, node):
                logger.warning("Skipping unhealthy node: %s", node)
                continue
            try:
                t0 = time.monotonic()
                logger.info("Inference -> %s  model=%s", node, effective_model)
                r = await client.post(
                    f"{node}/api/chat",
                    json={"model": effective_model, "messages": messages, "stream": False},
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                if r.status_code == 200:
                    content = r.json()["message"]["content"]
                    logger.info("Inference OK %s  %dms", node, latency_ms)
                    return {
                        "response": content,
                        "model": effective_model,
                        "node": node,
                        "latency_ms": latency_ms,
                    }
                else:
                    logger.warning("Node %s returned HTTP %s", node, r.status_code)
                    last_error = RuntimeError(f"HTTP {r.status_code} from {node}")
            except Exception as exc:
                logger.warning("Node %s error: type=%s repr=%r", node, type(exc).__name__, str(exc))
                last_error = exc

    raise RuntimeError(f"All inference nodes unavailable. Last error: {last_error}")
