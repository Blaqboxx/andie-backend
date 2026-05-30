# --- ANDIE API: MCP + Sentinel integration ---
# --- ANDIE API: MCP + Sentinel integration ---
import httpx
import json
import base64
from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException
from andie_backend.interfaces.api.event_bus import subscribe, unsubscribe, recent_events
from andie_backend.interfaces.api.workflow_engine import workflow_engine
import asyncio
import json as py_json
import time
import logging
import re
from autonomy.control_plane_metrics import control_plane_metrics

from andie_backend.inference.router import (
    chat as _ollama_chat,
    preflight_validate as _preflight_validate,
    resolve_inference_contract as _resolve_inference_contract,
    InferenceRouteError as _InferenceRouteError,
    InferenceTopologyError as _InferenceTopologyError,
)
from andie_backend.andie.brain.system_prompt import build_system_prompt
from andie_backend.andie.action.action_router import route as _action_route
from andie_backend.andie.memory.observer import observe as _observe
from andie_backend.andie.brain.competency_router import route_competencies
from andie_backend.andie.brain.response_composer import compose_andie_response
from andie_backend.andie.conversation import apply_conversational_cognition

router = APIRouter()
# --- ANDIE API: MCP + Sentinel integration ---


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue = await subscribe()

    class _RequestLike:
        def __init__(self, app):
            self.app = app

    bootstrap_request = _RequestLike(websocket.app)
    try:
        await websocket.send_json({
            "type": "connection.ready",
            "message": "ANDIE workspace event stream connected",
            "ts": int(time.time() * 1000),
        })
        await websocket.send_json({
            "type": "workspace.snapshot",
            "snapshot": _build_workspace_snapshot(bootstrap_request, limit=25),
            "ts": int(time.time() * 1000),
        })
        for event in recent_events(20):
            await websocket.send_text(py_json.dumps(event))

        disconnect_task = asyncio.create_task(websocket.receive())
        try:
            while True:
                queue_task = asyncio.create_task(queue.get())
                done, pending = await asyncio.wait(
                    {queue_task, disconnect_task},
                    timeout=30,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect_task in done:
                    queue_task.cancel()
                    break
                if queue_task in done:
                    event = queue_task.result()
                    await websocket.send_text(py_json.dumps(event))
                    continue
                queue_task.cancel()
                await websocket.send_json({"type": "workspace.heartbeat", "ts": int(time.time() * 1000)})
        finally:
            disconnect_task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        await unsubscribe(queue)

@router.websocket("/audio/stream")
async def audio_stream(websocket: WebSocket):
    await websocket.accept()
    chunks_received = 0
    bytes_received = 0

    try:
        while True:
            try:
                raw = await websocket.receive_text()
                data = py_json.loads(raw)
            except WebSocketDisconnect:
                return
            except Exception as parse_err:
                await websocket.send_json({"type": "error", "detail": f"invalid_payload: {parse_err}"})
                continue

            msg_type = str(data.get("type") or "").lower()

            if msg_type == "ping":
                await websocket.send_json({"type": "pong", "ts": int(time.time() * 1000)})
                continue

            if msg_type == "chunk":
                audio_b64 = data.get("audio")
                index = data.get("index")
                if not audio_b64:
                    await websocket.send_json({"type": "error", "detail": "missing_audio"})
                    continue

                try:
                    payload = base64.b64decode(audio_b64)
                except Exception:
                    await websocket.send_json({"type": "error", "detail": "invalid_base64_audio", "index": index})
                    continue

                chunks_received += 1
                bytes_received += len(payload)
                await websocket.send_json({
                    "type": "ack",
                    "index": index,
                    "chunks": chunks_received,
                    "bytes": bytes_received,
                })
                continue

            if msg_type == "flush":
                if chunks_received > 0:
                    await websocket.send_json({
                        "type": "transcript",
                        "text": "Voice input received. ASR transcript service not configured yet.",
                        "chunks": chunks_received,
                        "bytes": bytes_received,
                    })
                else:
                    await websocket.send_json({"type": "error", "detail": "no_audio_received"})
                continue

            await websocket.send_json({"type": "error", "detail": f"unsupported_type:{msg_type}"})

    except WebSocketDisconnect:
        return

@router.get("/tasks/stream")
async def tasks_stream(request: Request, limit: int = 20):
    from fastapi.responses import StreamingResponse
    import time
    async def event_generator():
        # Send recent events first
        for event in recent_events(limit):
            yield f"data: {py_json.dumps(event)}\n\n"
        # Then stream new events
        queue = await subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                yield f"data: {py_json.dumps(event)}\n\n"
        finally:
            await unsubscribe(queue)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

import os
import socket
MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:7001")
SENTINEL_ALERTS = "/home/jamai-jamison/Security-Sentinel/alerts.json"

async def send_to_mcp(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{MCP_URL}/event", json=payload)
        r.raise_for_status()
        return r.json()


async def _tcp_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        fut = asyncio.get_running_loop().run_in_executor(
            None,
            lambda: socket.create_connection((host, port), timeout=timeout),
        )
        conn = await asyncio.wait_for(fut, timeout=timeout + 0.2)
        conn.close()
        return True
    except Exception:
        return False


async def _http_ok(url: str, timeout: float = 0.8) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            return 200 <= resp.status_code < 500
    except Exception:
        return False


async def _ollama_telemetry(base_url: str, timeout: float = 1.5) -> dict:
    tags_url = f"{base_url.rstrip('/')}/api/tags"
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(tags_url)
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        if resp.status_code != 200:
            return {
                "ready": False,
                "endpoint": base_url,
                "latency_ms": elapsed_ms,
                "model_count": 0,
                "error": f"http_{resp.status_code}",
            }

        payload = resp.json()
        models = payload.get("models", []) if isinstance(payload, dict) else []
        return {
            "ready": True,
            "endpoint": base_url,
            "latency_ms": elapsed_ms,
            "model_count": len(models),
            "error": None,
        }
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "ready": False,
            "endpoint": base_url,
            "latency_ms": elapsed_ms,
            "model_count": 0,
            "error": str(e),
        }


@router.get("/healthz")
async def healthz(request: Request):
    # Lightweight, non-blocking readiness checks for orchestrators.
    redis_host = os.environ.get("REDIS_HOST", "redis")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    qdrant_host = os.environ.get("QDRANT_HOST", "qdrant")
    qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
    ollama_host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL") or ""

    memory_service = getattr(request.app.state, "memory_service", None)
    memory_ready = memory_service is not None
    vector_ready = bool(getattr(memory_service, "embeddings_enabled", False)) if memory_service is not None else False

    redis_ready = await _tcp_reachable(redis_host, redis_port)
    qdrant_ready = await _http_ok(f"http://{qdrant_host}:{qdrant_port}/")
    ollama_ready = await _http_ok(f"{ollama_host.rstrip('/')}/api/tags")

    degraded_mode = not vector_ready
    return {
        "api_ready": True,
        "memory_ready": memory_ready,
        "vector_ready": vector_ready,
        "ollama_ready": ollama_ready,
        "redis_ready": redis_ready,
        "qdrant_ready": qdrant_ready,
        "degraded_mode": degraded_mode,
    }

@router.get("/system/status")
async def system_status(request: Request):
    status = {"andie": "online"}

    # MCP check
    _mcp_url = os.environ.get("MCP_URL", "http://andie-mcp:7001")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{_mcp_url}/status")
            status["mcp"] = "online" if r.status_code == 200 else "unknown"
    except:
        status["mcp"] = "offline"

    # Sentinel check
    _sentinel_url = os.environ.get("SENTINEL_URL", "http://andie-sentinel:7002")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{_sentinel_url}/health")
            status["sentinel"] = "online" if r.status_code == 200 else "unknown"
    except:
        status["sentinel"] = "offline"

    # Ollama runtime telemetry — return a string status for UI compatibility.
    ollama_host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL") or ""
    _ollama_data = await _ollama_telemetry(ollama_host)
    if isinstance(_ollama_data, dict):
        status["ollama"] = "online" if _ollama_data.get("ready") else ("degraded" if not _ollama_data.get("error") else "offline")
        status["ollama_detail"] = _ollama_data
    else:
        status["ollama"] = str(_ollama_data)

    # Memory/embedding readiness — return string status + detail for UI.
    try:
        memory_service = getattr(request.app.state, "memory_service", None)
        if memory_service is not None and hasattr(memory_service, "health"):
            _mem_data = memory_service.health()
        else:
            _mem_data = {
                "embeddings_enabled": False,
                "degraded": True,
                "degraded_reason": "Memory service not initialized",
                "vector_entries": 0,
            }
    except Exception as e:
        _mem_data = {
            "embeddings_enabled": False,
            "degraded": True,
            "degraded_reason": str(e),
            "vector_entries": 0,
        }
    if isinstance(_mem_data, dict):
        status["memory"] = "degraded" if _mem_data.get("degraded") else "online"
        status["memory_detail"] = _mem_data
    else:
        status["memory"] = str(_mem_data)

    return status


@router.get("/system/alerts")
async def system_alerts():
    try:
        with open(SENTINEL_ALERTS) as f:
            return {"alerts": json.load(f)}
    except:
        return {"alerts": []}


@router.get("/system/agents")
async def system_agents():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{MCP_URL}/agents/state")
            return r.json()
    except:
        return {"agents": []}


@router.get("/system/gpu")
async def system_gpu(request: Request):
    monitor = getattr(request.app.state, "gpu_monitor", None)
    if monitor is None:
        return {"status": "unavailable", "reason": "gpu monitor not initialized"}
    return {"status": "ok", "gpu": monitor.snapshot()}


@router.get("/runtime/services")
async def runtime_services(request: Request):
    monitor = getattr(request.app.state, "gpu_monitor", None)
    speech = getattr(request.app.state, "speech_runtime", None)
    vision = getattr(request.app.state, "vision_runtime", None)
    memory_service = getattr(request.app.state, "memory_service", None)

    gpu = monitor.snapshot() if monitor is not None else {"accelerator": "none", "vram_gb_total": 0.0, "service_activation_matrix": {}}
    accelerator = gpu.get("accelerator", "none")
    vram_gb = float(gpu.get("vram_gb_total", 0.0) or 0.0)

    speech_status = speech.status(accelerator) if speech is not None else {"available": False, "engine": "unknown"}
    vision_status = vision.status(accelerator, vram_gb) if vision is not None else {"available": False}

    matrix = dict(gpu.get("service_activation_matrix", {}))
    matrix.update({
        "speech_asr": bool(speech_status.get("available", False)),
        "vision_ocr": bool(vision_status.get("available", False)),
        "vector_memory": bool(getattr(memory_service, "embeddings_enabled", False)) if memory_service is not None else False,
    })

    return {
        "status": "ok",
        "accelerator": accelerator,
        "gpu": gpu,
        "speech": speech_status,
        "vision": vision_status,
        "service_activation_matrix": matrix,
    }


@router.post("/runtime/route-task")
async def runtime_route_task(request: Request):
    data = await request.json()
    task_type = (data.get("task_type") or "quick_response").strip().lower()

    router = getattr(request.app.state, "task_router", None)
    monitor = getattr(request.app.state, "gpu_monitor", None)
    speech = getattr(request.app.state, "speech_runtime", None)
    vision = getattr(request.app.state, "vision_runtime", None)

    model = router.choose_model(task_type) if router is not None else "phi"
    gpu = monitor.snapshot() if monitor is not None else {"accelerator": "none", "vram_gb_total": 0.0, "service_activation_matrix": {}}
    accelerator = gpu.get("accelerator", "none")
    vram_gb = float(gpu.get("vram_gb_total", 0.0) or 0.0)

    speech_status = speech.status(accelerator) if speech else {}
    vision_status = vision.status(accelerator, vram_gb) if vision else {}

    return {
        "status": "ok",
        "task_type": task_type,
        "selected_model": model,
        "accelerator": accelerator,
        "services": {
            "speech_available": bool(speech_status.get("available", False)),
            "vision_available": bool(vision_status.get("available", False)),
        },
        "service_activation_matrix": gpu.get("service_activation_matrix", {}),
    }


@router.get("/guardian")
async def guardian_status():
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get("http://127.0.0.1:7010/health")
            if r.status_code == 200:
                return r.json()
            return {"guardian": "offline", "status_code": r.status_code}
    except Exception as e:
        return {"guardian": "offline", "error": str(e)}


# --- ANDIE API: Assistant endpoint ---
@router.post("/assist")
async def assist(req: dict):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{MCP_URL}/event",
            json={
                "type": "assist",
                "task": req.get("task")
            }
        )
        return r.json()


@router.post("/build")
async def build(req: dict):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{MCP_URL}/event",
            json={
                "type": "build",
                "name": req.get("name"),
                "code": req.get("code")
            }
        )
        return r.json()


async def _persist_chat_memory(request: Request, user_message: str, assistant_response: str, confidence: float, meta: dict, session_id: str | None = None):
    """Persist chat memory off the critical response path."""
    try:
        memory = request.app.state.memory_service
    except Exception:
        return

    def _write():
        memory.add({
            "role": "user",
            "content": user_message,
            "session_id": session_id,
            "output": {
                "channel": "chat",
                "event": "chat_user_turn",
                "session_id": session_id,
            },
        })
        memory.add({
            "role": "assistant",
            "content": assistant_response,
            "session_id": session_id,
            "output": {
                "channel": "chat",
                "event": "chat_assistant_turn",
                "session_id": session_id,
                "confidence": confidence,
                "meta": meta,
            },
        })

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write)
    except Exception:
        # Memory persistence is best-effort and must never block chat.
        return


_IDENTITY_PATTERNS = [
    re.compile(r"\bmy name is (?P<name>[A-Za-z][A-Za-z0-9' -]{0,60})", re.IGNORECASE),
    re.compile(r"\bcall me (?P<name>[A-Za-z][A-Za-z0-9' -]{0,60})", re.IGNORECASE),
    re.compile(r"\bi am (?P<name>[A-Za-z][A-Za-z0-9' -]{0,60})\b", re.IGNORECASE),
    re.compile(r"\bi'm (?P<name>[A-Za-z][A-Za-z0-9' -]{0,60})\b", re.IGNORECASE),
]


def _normalize_identity_value(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", (raw or "").strip(" .,!?:;\"'"))
    cleaned = re.sub(r"\b(and|but|because|please|thanks)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" .,!?:;\"'")
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered in {"andie", "assistant", "ai"}:
        return ""
    return " ".join(part[:1].upper() + part[1:] for part in cleaned.split(" ") if part)


def _extract_identity_facts(text: str) -> list[dict]:
    if not text:
        return []
    facts = []
    seen = set()
    for pattern in _IDENTITY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        name = _normalize_identity_value(match.group("name"))
        if not name:
            continue
        dedupe_key = ("user_name", name.lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        facts.append({
            "type": "identity",
            "key": "user_name",
            "value": name,
            "confidence": 0.98,
            "source": "explicit_user_statement",
        })
    return facts


async def _persist_identity_facts(request: Request, user_message: str, session_id: str | None = None) -> list[dict]:
    facts = _extract_identity_facts(user_message)
    if not facts:
        return []

    try:
        memory = request.app.state.memory_service
    except Exception:
        return facts

    def _write():
        for fact in facts:
            memory.add({
                "agent": "identity_memory",
                "input": f"Known user fact: {fact['key']}={fact['value']}",
                "content": f"Known user fact: {fact['key']}={fact['value']}",
                "session_id": session_id,
                "output": {**fact, "channel": "chat", "session_id": session_id},
            })

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write)
    except Exception:
        return facts
    return facts


def _extract_identity_from_entry(entry: dict) -> tuple[str, str] | None:
    output = entry.get("output")
    if isinstance(output, dict) and output.get("type") == "identity":
        key = str(output.get("key") or "").strip()
        value = _normalize_identity_value(str(output.get("value") or ""))
        if key and value:
            return key, value

    agent = entry.get("agent") or entry.get("role")
    if agent not in {"user", "identity_memory"}:
        return None

    text = entry.get("input") or entry.get("content") or ""
    for fact in _extract_identity_facts(text):
        return fact["key"], fact["value"]
    return None


def _build_known_user_facts(request: Request, current_facts: list[dict] | None = None) -> str:
    known = {}
    for fact in current_facts or []:
        key = str(fact.get("key") or "").strip()
        value = _normalize_identity_value(str(fact.get("value") or ""))
        if key and value:
            known.setdefault(key, value)

    try:
        memory = request.app.state.memory_service
    except Exception:
        memory = None

    if memory is not None and hasattr(memory, "get_recent"):
        try:
            recent = list(reversed(memory.get_recent(limit=80)))
        except Exception:
            recent = []
        for entry in recent:
            parsed = _extract_identity_from_entry(entry)
            if not parsed:
                continue
            key, value = parsed
            known.setdefault(key, value)

    lines = []
    if known.get("user_name"):
        lines.append(f"- User name: {known['user_name']}")
    return "\n".join(lines)


# --- ANDIE API: Chat Endpoint ---
@router.post("/chat")
async def chat(request: Request):
    try:
        data = await request.json()
        user_message = data.get("message", "")
        session_id = (
            request.headers.get("x-session-id")
            or request.headers.get("x-conversation-id")
            or getattr(getattr(request, "client", None), "host", None)
            or "default"
        )
        current_facts = await _persist_identity_facts(request, user_message, session_id=str(session_id))
        known_user_facts = _build_known_user_facts(request, current_facts=current_facts)
        _sys = _build_orchestrator_prompt("", known_user_facts=known_user_facts)
        chat_timeout_s = float(os.environ.get("ANDIE_CHAT_TIMEOUT_SECONDS", "20"))
        try:
            result = await asyncio.wait_for(
                _action_route(user_message, _ollama_chat, _sys),
                timeout=chat_timeout_s,
            )
        except asyncio.TimeoutError:
            return {
                "status": "error",
                "response": f"Chat timed out after {int(chat_timeout_s)}s. Please try again.",
                "confidence": 0.0,
                "intent": "TIMEOUT",
                "meta": {"source": "timeout_guard"},
            }
        if "meta" not in result:
            result["meta"] = {"source": "ollama", "intent": result.get("intent", "CHAT_RESPONSE")}
        result.setdefault("confidence", 0.92)

        try:
            runtime_snapshot = _build_workspace_snapshot(request, limit=25)
        except Exception:
            runtime_snapshot = {}

        result = apply_conversational_cognition(
            request=request,
            user_message=user_message,
            llm_result=result,
            runtime_snapshot=runtime_snapshot,
            session_id=str(session_id),
        )

        competency_weights = route_competencies(user_message)
        pressure_tier = _pressure_tier_from_snapshot(runtime_snapshot)
        result = compose_andie_response(
            user_message=user_message,
            llm_result=result,
            competency_weights=competency_weights,
            pressure_tier=pressure_tier,
        )


        # Store structured memory asynchronously; never block response path.
        asyncio.create_task(
            _persist_chat_memory(
                request,
                user_message=user_message,
                assistant_response=result.get("response", ""),
                confidence=float(result.get("confidence", 0.0) or 0.0),
                meta=result.get("meta") or {},
                session_id=str(session_id),
            )
        )

        out = {k: v for k, v in result.items() if v is not None}
        out["status"] = "ok"
        if "response" not in out:
            out["response"] = ""
        # Non-blocking observation — never affects chat response
        asyncio.create_task(_observe(user_message, out.get("response", "")))
        return out

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "response": f"[ANDIE error] {type(e).__name__}: {e}", "confidence": 0.0}


# Helper to run repair agent async (handles both sync/async run methods)
def _truncate_to_sentence_boundary(text: str, max_chars: int) -> str:
    """Trim to max_chars and snap to sentence boundary when possible."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text

    clipped = text[:max_chars].rstrip()
    # Prefer nearest sentence terminator in the clipped window.
    end_idx = max(clipped.rfind('.'), clipped.rfind('!'), clipped.rfind('?'))
    if end_idx >= int(max_chars * 0.5):
        return clipped[:end_idx + 1].strip()

    # Fallback to last whitespace to avoid mid-word cuts.
    ws_idx = clipped.rfind(' ')
    if ws_idx >= int(max_chars * 0.5):
        return clipped[:ws_idx].strip() + '...'
    return clipped + '...'


async def async_run_repair(repair):
    try:
        run_method = getattr(repair, "run", None)
        if run_method:
            if asyncio.iscoroutinefunction(run_method):
                await run_method("andie_backend")
            else:
                # Run sync in thread to avoid blocking event loop
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, run_method, "andie_backend")
    except Exception as e:
        print(f"[Repair Error] {e}")

ORCH_LOGGER = logging.getLogger("andie.orchestrator")
_STREAM_METRICS = {
    "requests": 0,
    "errors": 0,
    "dropped_streams": 0,
    "disconnects": 0,
    "tokens": 0,
    "token_seconds": 0.0,
    "first_token_ms": [],
    "total_ms": [],
    "stream_duration_ms": [],
}


def _build_orchestrator_prompt(context: str, known_user_facts: str = "") -> str:
    prompt = (
        "You are ANDIE (Autonomous Neural Distributed Intelligence Engine), an AI system "
        "running on a distributed home cluster. You are concise, direct, and technically "
        "precise. You assist the operator Jamai with system management, code, and autonomous "
        "tasks. Never pretend to be a generic AI assistant. "
        "Identity rules: ANDIE is the assistant name. The user's identity is always separate "
        "from the assistant identity. Never answer that the user's name is ANDIE unless the "
        "known user facts explicitly state that."
    )
    if known_user_facts:
        prompt = prompt + "\nKnown user facts:\n" + known_user_facts
    if context:
        prompt = prompt + "\nAdditional context:\n" + str(context)
    return prompt


def _orchestrator_policy() -> dict:
    return {
        "server_timeout_s": float(os.environ.get("ANDIE_ORCHESTRATOR_SERVER_TIMEOUT_SECONDS", "25")),
        "num_predict": int(os.environ.get("ANDIE_ORCHESTRATOR_NUM_PREDICT", "32")),
        "temperature": float(os.environ.get("ANDIE_ORCHESTRATOR_TEMPERATURE", "0.2")),
        "read_timeout_s": float(os.environ.get("ANDIE_ORCHESTRATOR_READ_TIMEOUT_SECONDS", "30")),
        "max_chars": int(os.environ.get("ANDIE_ORCHESTRATOR_MAX_RESPONSE_CHARS", "320")),
    }


def _estimate_token_count(text: str) -> int:
    return len([x for x in (text or "").split() if x])


def _append_metric(bucket: str, value: float):
    arr = _STREAM_METRICS[bucket]
    arr.append(float(value))
    if len(arr) > 2000:
        del arr[: len(arr) - 2000]


def _record_stream_metrics(*, first_token_ms: int | None, total_ms: int, stream_duration_ms: int, token_count: int, ok: bool):
    _STREAM_METRICS["requests"] += 1
    if not ok:
        _STREAM_METRICS["errors"] += 1
    if first_token_ms is not None:
        _append_metric("first_token_ms", first_token_ms)
    _append_metric("total_ms", total_ms)
    _append_metric("stream_duration_ms", stream_duration_ms)
    _STREAM_METRICS["tokens"] += max(token_count, 0)
    _STREAM_METRICS["token_seconds"] += max(stream_duration_ms, 0) / 1000.0


@router.post("/workflow/run")
async def workflow_run(request: Request):
    data = await request.json()
    task = data.get("task", data.get("message", ""))
    if not task:
        raise HTTPException(status_code=400, detail="task is required")

    context = data.get("context", "")
    steps = data.get("workflow")
    allow_recovery = bool(data.get("allowRecovery", False))
    memory = data.get("memory")

    return workflow_engine.run_workflow(
        task=str(task),
        steps=steps if isinstance(steps, list) else None,
        context_text=str(context or ""),
        memory=memory if isinstance(memory, dict) else None,
        allow_recovery=allow_recovery,
    )

@router.post("/orchestrator/run")
async def orchestrator_run(request: Request):
    started = time.monotonic()
    policy = _orchestrator_policy()
    try:
        data = await request.json()
        task = data.get("task", data.get("message", ""))
        context = data.get("context", "")

        if "workflow" in str(task).lower():
            workflow_id = f"wf-{int(time.time() * 1000)}"
            asyncio.create_task(
                workflow_engine.run_workflow_stream(
                    task=str(task),
                    workflow_id=workflow_id,
                    context_text=str(context or ""),
                )
            )
            return {
                "type": "workflow",
                "status": "started",
                "task": task,
                "route": "thinkpad",
                "workflowId": workflow_id,
                "result": {
                    "streaming": True,
                    "workflowId": workflow_id,
                },
            }

        current_facts = await _persist_identity_facts(request, task)
        known_user_facts = _build_known_user_facts(request, current_facts=current_facts)
        system_prompt = _build_orchestrator_prompt(context, known_user_facts=known_user_facts)
        contract = _resolve_inference_contract()

        result = await asyncio.wait_for(
            _ollama_chat(
                messages=[{"role": "user", "content": task}],
                system=system_prompt,
                num_predict=policy["num_predict"],
                temperature=policy["temperature"],
                read_timeout_seconds=policy["read_timeout_s"],
            ),
            timeout=policy["server_timeout_s"],
        )

        total_ms = int((time.monotonic() - started) * 1000)
        raw_response = result.get("response", "")
        trimmed_response = _truncate_to_sentence_boundary(raw_response, policy["max_chars"])

        fallback_used = bool(result.get("fallback_used", False) or (result.get("routing") or {}).get("fallback_used", False))
        topology_verified = bool(result.get("topology_verified", False))
        resolved_node = str(result.get("node") or contract.get("resolved_node") or "")
        resolved_model = str(result.get("model") or contract.get("resolved_model") or "")

        if fallback_used:
            msg = "Unexpected fallback activation in strict production mode"
            ORCH_LOGGER.critical("[ORCH-TOPOLOGY] %s", msg)
            raise _InferenceTopologyError(msg, diagnostics={"fallback_used": True, **contract})

        if not topology_verified:
            msg = "Topology verification failed"
            ORCH_LOGGER.critical("[ORCH-TOPOLOGY] %s node=%s model=%s", msg, resolved_node, resolved_model)
            raise _InferenceTopologyError(
                msg,
                diagnostics={
                    "resolved_node": resolved_node,
                    "resolved_model": resolved_model,
                    "topology_verified": False,
                    "fallback_used": False,
                    **contract,
                },
            )

        orchestrator_payload = {
            "status": "ok",
            "response": trimmed_response,
            "confidence": 0.92,
            "route": "assistant",
            "node": resolved_node,
            "model": resolved_model,
            "fallback_used": False,
            "topology_verified": True,
            "timings_ms": {
                "total": total_ms,
                "probe": (result.get("timings_ms") or {}).get("probe"),
                "inference": (result.get("timings_ms") or {}).get("inference", result.get("latency_ms")),
            },
            "meta": {
                "source": "ollama",
                "model": resolved_model,
                "node": resolved_node,
                "fallback_used": False,
                "topology_verified": True,
                "timings_ms": {
                    "total": total_ms,
                    "probe": (result.get("timings_ms") or {}).get("probe"),
                    "inference": (result.get("timings_ms") or {}).get("inference", result.get("latency_ms")),
                },
                "routing": result.get("routing") or {"fallback_used": False, "attempt_count": 1, "selected_node": resolved_node, "attempts": []},
                "policy": {
                    **policy,
                    "truncated": trimmed_response != raw_response,
                },
            },
        }

        competency_weights = route_competencies(task)
        return compose_andie_response(
            user_message=task,
            llm_result=orchestrator_payload,
            competency_weights=competency_weights,
        )
    except asyncio.TimeoutError:
        total_ms = int((time.monotonic() - started) * 1000)
        ORCH_LOGGER.critical("[ORCH-TOPOLOGY] run timeout exceeded: %ss", policy["server_timeout_s"])
        contract = _resolve_inference_contract()
        return {
            "status": "error",
            "response": f"Orchestrator timed out after {int(policy['server_timeout_s'])}s.",
            "confidence": 0.0,
            "node": contract.get("resolved_node"),
            "model": contract.get("resolved_model"),
            "fallback_used": False,
            "topology_verified": False,
            "timings_ms": {"total": total_ms, "probe": None, "inference": None},
            "meta": {
                "source": "ollama",
                "error_type": "ServerTimeout",
                "node": contract.get("resolved_node"),
                "model": contract.get("resolved_model"),
                "fallback_used": False,
                "topology_verified": False,
                "timings_ms": {"total": total_ms, "probe": None, "inference": None},
                "routing": {"fallback_used": False, "attempt_count": 0, "selected_node": None, "attempts": []},
                "policy": policy,
            },
        }
    except Exception as e:
        total_ms = int((time.monotonic() - started) * 1000)
        diagnostics = getattr(e, "diagnostics", {}) or {}
        resolved_node = diagnostics.get("resolved_node") or diagnostics.get("base_url") or diagnostics.get("expected_node")
        resolved_model = diagnostics.get("resolved_model") or diagnostics.get("expected_model")
        fallback_used = bool(diagnostics.get("fallback_used", False))
        topology_verified = bool(diagnostics.get("topology_verified", False))
        ORCH_LOGGER.critical("[ORCH-TOPOLOGY] run failure type=%s err=%s diag=%s", type(e).__name__, str(e), diagnostics)
        return {
            "status": "error",
            "response": str(e),
            "confidence": 0.0,
            "node": resolved_node,
            "model": resolved_model,
            "fallback_used": fallback_used,
            "topology_verified": topology_verified,
            "timings_ms": {
                "total": total_ms,
                "probe": diagnostics.get("probe_ms"),
                "inference": diagnostics.get("request_ms"),
            },
            "meta": {
                "source": "ollama",
                "error_type": type(e).__name__,
                "node": resolved_node,
                "model": resolved_model,
                "fallback_used": fallback_used,
                "topology_verified": topology_verified,
                "timings_ms": {
                    "total": total_ms,
                    "probe": diagnostics.get("probe_ms"),
                    "inference": diagnostics.get("request_ms"),
                },
                "routing": {
                    "fallback_used": fallback_used,
                    "attempt_count": len(diagnostics.get("attempts", []) or []),
                    "selected_node": diagnostics.get("selected_node"),
                    "attempts": diagnostics.get("attempts", []) or [],
                    "nodes": diagnostics.get("nodes", []) or [],
                },
            },
        }


@router.post("/orchestrator/stream")
async def orchestrator_stream(request: Request):
    """Token-streaming orchestrator endpoint (SSE) with strict parity to run."""
    from fastapi.responses import StreamingResponse

    data = await request.json()
    task = data.get("task", data.get("message", ""))
    context = data.get("context", "")
    current_facts = await _persist_identity_facts(request, task)
    known_user_facts = _build_known_user_facts(request, current_facts=current_facts)
    system_prompt = _build_orchestrator_prompt(context, known_user_facts=known_user_facts)
    policy = _orchestrator_policy()

    preflight = await _preflight_validate(read_timeout_seconds=policy["read_timeout_s"])
    resolved_node = preflight["resolved_node"]
    resolved_model = preflight["resolved_model"]

    timeout = httpx.Timeout(
        connect=float(os.environ.get("ANDIE_OLLAMA_CONNECT_TIMEOUT_SECONDS", "5")),
        read=policy["read_timeout_s"],
        write=float(os.environ.get("ANDIE_OLLAMA_WRITE_TIMEOUT_SECONDS", "5")),
        pool=float(os.environ.get("ANDIE_OLLAMA_POOL_TIMEOUT_SECONDS", "10")),
    )

    started = time.monotonic()

    async def event_generator():
        first_token_ms = None
        total_ms = 0
        token_count = 0
        stream_duration_ms = 0
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{resolved_node}/api/chat",
                    json={
                        "model": resolved_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": task},
                        ],
                        "stream": True,
                        "options": {
                            "num_predict": policy["num_predict"],
                            "temperature": policy["temperature"],
                        },
                    },
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        total_ms = int((time.monotonic() - started) * 1000)
                        _record_stream_metrics(first_token_ms=first_token_ms, total_ms=total_ms, stream_duration_ms=0, token_count=token_count, ok=False)
                        ORCH_LOGGER.critical("[ORCH-TOPOLOGY] stream HTTP failure status=%s node=%s model=%s", resp.status_code, resolved_node, resolved_model)
                        yield f"data: {py_json.dumps({'type': 'error', 'error': 'stream_http_failure', 'status_code': resp.status_code, 'body': body.decode('utf-8', 'ignore')[:300], 'node': resolved_node, 'model': resolved_model, 'fallback_used': False, 'topology_verified': False, 'timings_ms': {'total': total_ms, 'first_token_ms': first_token_ms, 'stream_duration_ms': 0, 'token_count': token_count, 'tokens_per_second': 0.0}})}\n\n"
                        return

                    yield f"data: {py_json.dumps({'type': 'start', 'model': resolved_model, 'node': resolved_node, 'fallback_used': False, 'topology_verified': True, 'policy': policy})}\n\n"

                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            item = py_json.loads(line)
                        except Exception:
                            continue

                        token_chunk = ((item.get("message") or {}).get("content") or "")
                        if token_chunk:
                            token_count += _estimate_token_count(token_chunk)
                            if first_token_ms is None:
                                first_token_ms = int((time.monotonic() - started) * 1000)
                            yield f"data: {py_json.dumps({'type': 'token', 'chunk': token_chunk})}\n\n"

                        if item.get("done"):
                            total_ms = int((time.monotonic() - started) * 1000)
                            stream_duration_ms = max(0, total_ms - (first_token_ms or total_ms))
                            tps = round((token_count / (stream_duration_ms / 1000.0)), 2) if stream_duration_ms > 0 else 0.0
                            _record_stream_metrics(first_token_ms=first_token_ms, total_ms=total_ms, stream_duration_ms=stream_duration_ms, token_count=token_count, ok=True)
                            ORCH_LOGGER.info("[ORCH-STREAM] done node=%s model=%s first_token_ms=%s total_ms=%s stream_duration_ms=%s token_count=%s tps=%s", resolved_node, resolved_model, first_token_ms, total_ms, stream_duration_ms, token_count, tps)
                            yield f"data: {py_json.dumps({'type': 'done', 'node': resolved_node, 'model': resolved_model, 'fallback_used': False, 'topology_verified': True, 'timings_ms': {'first_token_ms': first_token_ms, 'total_ms': total_ms, 'stream_duration_ms': stream_duration_ms, 'token_count': token_count, 'tokens_per_second': tps}})}\n\n"
                            return
        except Exception as e:
            total_ms = int((time.monotonic() - started) * 1000)
            stream_duration_ms = max(0, total_ms - (first_token_ms or total_ms))
            tps = round((token_count / (stream_duration_ms / 1000.0)), 2) if stream_duration_ms > 0 else 0.0
            _record_stream_metrics(first_token_ms=first_token_ms, total_ms=total_ms, stream_duration_ms=stream_duration_ms, token_count=token_count, ok=False)
            ORCH_LOGGER.critical("[ORCH-TOPOLOGY] stream exception type=%s err=%s", type(e).__name__, str(e))
            yield f"data: {py_json.dumps({'type': 'error', 'error': str(e), 'node': resolved_node, 'model': resolved_model, 'fallback_used': False, 'topology_verified': False, 'timings_ms': {'first_token_ms': first_token_ms, 'total_ms': total_ms, 'stream_duration_ms': stream_duration_ms, 'token_count': token_count, 'tokens_per_second': tps}})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@router.post("/converse")
async def converse(request: Request):
    return await orchestrator_run(request)

@router.post("/chat/run")
async def chat_run(request: Request):
    return await orchestrator_run(request)


import json as _json
from pathlib import Path as _Path
_SKILLS_DIR = _Path("/media/jamai-jamison/78eb7352-2efe-465a-a250-c5df9c24726d/valhalla/skills")

@router.post("/build/autonomous")
async def autonomous_build(request: Request):
    from andie_backend.build.engine import run_build
    data = await request.json()
    brief = data.get("brief", "")
    max_iterations = int(data.get("maxIterations", 5))
    if not brief:
        raise HTTPException(status_code=400, detail="brief is required")
    logs = []
    async def emit(event):
        logs.append(event)
    try:
        result = await run_build(brief, max_iterations, emit=emit)
        result["logs"] = logs
        return result
    except Exception as e:
        import traceback
        print("[BUILD ERROR]", str(e))
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/build/skills")
async def list_skills():
    skills = []
    if _SKILLS_DIR.exists():
        for d in _SKILLS_DIR.iterdir():
            mp = d / "manifest.json"
            if mp.exists():
                try:
                    skills.append(_json.loads(mp.read_text()))
                except:
                    pass
    return {"skills": skills}


# ── Missing endpoints ─────────────────────────────────────────────────────────


@router.get("/metrics")
async def metrics():
    import psutil

    def _percentile(values, pct):
        if not values:
            return None
        vals = sorted(values)
        idx = (len(vals) - 1) * pct
        lo = int(idx)
        hi = min(lo + 1, len(vals) - 1)
        frac = idx - lo
        return round(vals[lo] * (1 - frac) + vals[hi] * frac, 2)

    first_token = list(_STREAM_METRICS["first_token_ms"])
    total = list(_STREAM_METRICS["total_ms"])
    stream_dur = list(_STREAM_METRICS["stream_duration_ms"])
    token_seconds = _STREAM_METRICS["token_seconds"]
    tokens = _STREAM_METRICS["tokens"]

    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
        "memory_available_mb": psutil.virtual_memory().available // 1024 // 1024,
        "disk_percent": psutil.disk_usage("/").percent,
        "orchestrator_stream": {
            "requests": _STREAM_METRICS["requests"],
            "errors": _STREAM_METRICS["errors"],
            "dropped_streams": _STREAM_METRICS["dropped_streams"],
            "disconnects": _STREAM_METRICS["disconnects"],
            "first_token_ms": {
                "p50": _percentile(first_token, 0.5),
                "p95": _percentile(first_token, 0.95),
                "p99": _percentile(first_token, 0.99),
            },
            "total_ms": {
                "p50": _percentile(total, 0.5),
                "p95": _percentile(total, 0.95),
                "p99": _percentile(total, 0.99),
            },
            "stream_duration_ms": {
                "p50": _percentile(stream_dur, 0.5),
                "p95": _percentile(stream_dur, 0.95),
                "p99": _percentile(stream_dur, 0.99),
            },
            "token_count_total": tokens,
            "tokens_per_second": round(tokens / token_seconds, 2) if token_seconds > 0 else 0.0,
        },
    }

@router.get("/nodes/status")
async def nodes_status():
    return {
        "nodes": [
            {"id": "blaqtower", "host": "100.68.116.93", "role": "compute", "status": "online"},
            {"id": "blaqboxx", "host": "100.115.129.80", "role": "inference", "status": "online"},
        ]
    }

@router.post("/agent/{name}")
async def run_agent_by_name(name: str, request: Request):
    data = await request.json()
    requested = str(name or "").strip()
    normalized = requested.lower()

    alias_map = {"cryptonia_historical_agent": "coinmarketcap_agent"}
    resolved = alias_map.get(normalized, normalized)

    if resolved in {"coinmarketcap_agent", "frontend_ui_agent"}:
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        prompt = str(data.get("input") or data.get("task") or "")

        if resolved == "coinmarketcap_agent":
            from andie.core.agents.coinmarketcap_agent import run_agent as _run_coinmarketcap
            metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
            result = _run_coinmarketcap({"prompt": prompt, "metadata": metadata})
        else:
            from andie.core.agents.frontend_ui_agent import run_agent as _run_frontend
            metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
            context = str(params.get("context") or "")
            result = _run_frontend({"prompt": prompt, "context": context, "metadata": metadata})

        return {
            "status": "executed",
            "agentResolution": {"requested": requested, "resolved": resolved},
            "result": result,
        }

    task = data.get("task", f"Run {name} agent")
    system = f"You are {name}, an AI agent. Respond concisely."
    try:
        result = await _ollama_chat(messages=[{"role": "user", "content": task}], system=system)
        return {"agent": name, "response": result["response"], "status": "ok"}
    except Exception as e:
        return {"agent": name, "response": f"{name} agent unavailable: {e}", "status": "error"}
@router.get("/skills")
async def list_all_skills():
    try:
        from skills import register_builtin_skills
        from skills.registry import registry

        register_builtin_skills()
        skills = [
            {
                "name": skill.name,
                "description": skill.description,
                "risk": skill.risk_level,
                "requires_approval": skill.requires_approval,
                "depends_on": list(skill.depends_on or []),
                "keywords": list(skill.keywords or []),
            }
            for skill in registry.list()
        ]
        return {"skills": skills, "count": len(skills)}
    except Exception as e:
        return {"skills": [], "count": 0, "error": str(e)}
@router.get("/timeline")
async def timeline():
    return {"events": [], "message": "Timeline coming in Phase 2"}

@router.get("/capital")
async def capital():
    return {"status": "active", "balance": 0, "message": "Capital Control active"}

@router.get("/memory/snapshot")
async def memory_snapshot(limit: int = 8):
    return {"episodic": [], "semantic": [], "procedural": [], "message": "snapshot active"}

@router.post("/memory/save-session")
async def save_session():
    return {"status": "ok", "message": "session saved"}

# Compatibility runtime state for autonomy control endpoints.
_autonomy_state = {"running": False, "enabled": True, "iteration": 0}


# ── Real Autonomy Engine endpoints ───────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, '/media/jamai-jamison/78eb7352-2efe-465a-a250-c5df9c24726d/valhalla/andie_backend')

@router.get("/autonomy/status")
async def autonomy_status():
    try:
        from autonomy.autonomy_controller import decide_execution_mode, AUTO_THRESHOLD, REVIEW_THRESHOLD
        from autonomy.autonomy_profiles import DEFAULT_PROFILE
        return {
            "status": "active",
            "mode": "assisted",
            "profile": DEFAULT_PROFILE,
            "auto_threshold": AUTO_THRESHOLD,
            "review_threshold": REVIEW_THRESHOLD,
            "enabled": True,
        }
    except Exception as e:
        return {"status": "active", "mode": "supervised", "enabled": True, "error": str(e)}

@router.get("/autonomy/trust")
async def autonomy_trust(skill: str = "default"):
    try:
        from autonomy.trust_engine import compute_trust
        trust = compute_trust(skill)
        return {"skill": skill, "trust": trust, "status": "ok"}
    except Exception as e:
        return {"skill": skill, "trust": 0.75, "status": "error", "error": str(e)}

@router.post("/autonomy/decide")
async def autonomy_decide(request: Request):
    try:
        from autonomy.autonomy_controller import decide_execution_mode
        data = await request.json()
        step = data.get("step", {})
        mode = data.get("global_mode", "assisted")
        result = decide_execution_mode(step, global_mode=mode)
        return {"decision": result, "step": step, "global_mode": mode}
    except Exception as e:
        return {"decision": "approval", "error": str(e)}

@router.get("/autonomy/guardrails")
async def autonomy_guardrails():
    try:
        from autonomy.governance import evaluate_go_no_go
        from autonomy.autonomy_profiles import PROFILES, DEFAULT_PROFILE
        profile = PROFILES.get(DEFAULT_PROFILE, {})
        return {
            "status": "active",
            "profile": DEFAULT_PROFILE,
            "rules": [
                {"name": "auto_threshold", "value": profile.get("auto_threshold", 0.8)},
                {"name": "review_threshold", "value": profile.get("review_threshold", 0.5)},
                {"name": "high_risk_threshold", "value": 0.85},
            ]
        }
    except Exception as e:
        return {"status": "active", "rules": [], "error": str(e)}

@router.get("/autonomy/decision/latest")
async def autonomy_decision_latest():
    try:
        from autonomy.decision_engine import DecisionEngine
        engine = DecisionEngine()
        return {"decision": "idle", "confidence": 0.0, "timestamp": "now", "engine": "active"}
    except Exception as e:
        return {"decision": "idle", "confidence": 0.0, "timestamp": "now"}

@router.get("/autonomy/learning")
async def autonomy_learning():
    try:
        from autonomy.learning_engine import memory
        data = memory.data if hasattr(memory, 'data') else {}
        return {"entries": len(data), "status": "active"}
    except Exception as e:
        return {"entries": 0, "status": "error", "error": str(e)}


@router.post("/autonomy/outcome")
async def autonomy_outcome(request: Request):
    try:
        try:
            from andie_backend.interfaces.api.event_bus import emit_event
            from interfaces.api.outcome_tracking import record_skill_outcome_internal
        except ModuleNotFoundError:
            from interfaces.api.event_bus import emit_event
            from interfaces.api.outcome_tracking import record_skill_outcome_internal
        import uuid

        data = await request.json()
        execution_id = str(data.get("execution_id") or data.get("correlation_id") or uuid.uuid4())
        payload = record_skill_outcome_internal(
            skill_name=data.get("skill") or data.get("skill_name") or "",
            result=data.get("result") or "",
            context_key=data.get("context_key"),
            replaced_from=data.get("replaced_from"),
            latency=data.get("latency"),
            error=data.get("error"),
            record_execution=bool(data.get("record_execution", True)),
            source=data.get("source") or "live",
            intent_type=data.get("intent_type"),
            governance_profile=data.get("governance_profile"),
            effectiveness_score=data.get("effectiveness_score"),
            portfolio_group=data.get("portfolio_group"),
        )

        weight_update = (payload or {}).get("outcome_weight_update") or {}
        if weight_update.get("event"):
            await emit_event(
                {
                    "type": weight_update.get("event"),
                    "execution_id": execution_id,
                    "timestamp": int(time.time() * 1000),
                    "intent_type": payload.get("intent_type"),
                    "governance_profile": payload.get("governance_profile"),
                    "portfolio_group": payload.get("portfolio_group"),
                    "registry": weight_update.get("registry"),
                }
            )

        trend_update = (payload or {}).get("effectiveness_trend_update") or {}
        for update_key in ("baseline_update", "trend_update", "window_rotation_update"):
            update = trend_update.get(update_key) or {}
            if not update.get("event"):
                continue
            event_payload = {
                "type": update.get("event"),
                "execution_id": execution_id,
                "timestamp": int(time.time() * 1000),
            }
            for key, value in update.items():
                if key == "event":
                    continue
                event_payload[key] = value
            await emit_event(event_payload)

        return {
            "status": "ok",
            "execution_id": execution_id,
            "outcome": payload,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/autonomy/effectiveness/portfolio/{portfolio_group}")
async def autonomy_effectiveness_portfolio(portfolio_group: str):
    try:
        try:
            from andie_backend.autonomy.learning_engine import memory
        except ModuleNotFoundError:
            from autonomy.learning_engine import memory
        rollup = memory.get_effectiveness_portfolio_rollup(portfolio_group)
        return {
            "status": "ok",
            "portfolio_group": rollup.get("portfolio_group"),
            "rollup": rollup,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/autonomy/effectiveness/governance/{governance_profile}")
async def autonomy_effectiveness_governance(governance_profile: str):
    try:
        try:
            from andie_backend.autonomy.learning_engine import memory
        except ModuleNotFoundError:
            from autonomy.learning_engine import memory
        rollup = memory.get_effectiveness_governance_rollup(governance_profile)
        return {
            "status": "ok",
            "governance_profile": rollup.get("governance_profile"),
            "rollup": rollup,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/autonomy/effectiveness/summary")
async def autonomy_effectiveness_summary():
    try:
        try:
            from andie_backend.autonomy.learning_engine import memory
        except ModuleNotFoundError:
            from autonomy.learning_engine import memory
        summary = memory.get_effectiveness_summary()
        return {
            "status": "ok",
            "summary": summary,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@router.get("/autonomy/explain")
async def autonomy_explain():
    try:
        from autonomy.explainer import explain_decision, LAST_DECISION_CONTEXT
        result = explain_decision(LAST_DECISION_CONTEXT)
        if isinstance(result, dict) and "status" not in result:
            result = {"status": "ok", **result}
        return result
    except Exception as e:
        return {"status": "empty", "decision": None, "reasoning": [], "error": str(e)}
@router.get("/autonomy/rules")
async def autonomy_rules():
    try:
        from autonomy.governance import evaluate_go_no_go
        from autonomy.autonomy_profiles import PROFILES, DEFAULT_PROFILE
        profile = PROFILES.get(DEFAULT_PROFILE, {})
        return {
            "profile": DEFAULT_PROFILE,
            "rules": [
                {"id": "auto_threshold", "name": "Auto Execute Threshold", "value": profile.get("auto_threshold", 0.75), "enabled": True},
                {"id": "review_threshold", "name": "Review Threshold", "value": profile.get("review_threshold", 0.5), "enabled": True},
                {"id": "high_risk", "name": "High Risk Auto Threshold", "value": 0.85, "enabled": True},
            ]
        }
    except Exception as e:
        return {"rules": [], "error": str(e)}

@router.post("/autonomy/rules")
async def autonomy_rules_update(request: Request):
    data = await request.json()
    return {"status": "ok", "updated": data}

@router.post("/autonomy/rules/reload")
async def autonomy_rules_reload():
    return {"status": "reloaded"}

@router.post("/autonomy/rules/validate")
async def autonomy_rules_validate(request: Request):
    data = await request.json()
    return {"status": "valid", "rules": data}

@router.post("/autonomy/rules/simulate")
async def autonomy_rules_simulate(request: Request):
    data = await request.json()
    return {"status": "simulated", "result": "approval", "data": data}

@router.get("/autonomy/config")
async def autonomy_config():
    from autonomy.runtime_config import get_runtime_config
    return {"config": get_runtime_config()}

@router.post("/autonomy/config")
async def autonomy_config_update(request: Request):
    from autonomy.runtime_config import update_runtime_config
    data = await request.json()
    config = update_runtime_config(data if isinstance(data, dict) else {})
    return {"status": "updated", "config": config}

@router.post("/autonomy/profile")
async def autonomy_profile_update(profile: str):
    from autonomy.autonomy_profiles import PROFILES
    from autonomy.runtime_config import update_runtime_config
    normalized = str(profile or "").strip().lower()
    if normalized not in PROFILES:
        raise HTTPException(status_code=400, detail="unknown profile")
    config = update_runtime_config({"profile": normalized})
    return {"status": "updated", "profile": config.get("profile")}

@router.get("/autonomy/drift")
async def autonomy_drift():
    from autonomy.runtime_config import get_runtime_config
    config = get_runtime_config()
    return {
        "drift_detected": bool(config.get("drift_detected", False)),
        "forced_mode": config.get("forced_mode"),
        "drift_reason": config.get("drift_reason"),
        "drift_intensity": float(config.get("drift_intensity", 0.0) or 0.0),
        "drift_severity": config.get("drift_severity") or "stable",
        "metrics": control_plane_metrics.snapshot(),
    }

@router.post("/autonomy/safe-mode/reset")
async def autonomy_safe_mode_reset():
    from autonomy.runtime_config import update_runtime_config, get_runtime_config
    update_runtime_config({"forced_mode": None, "drift_detected": False, "drift_reason": None, "drift_intensity": 0.0, "drift_severity": "stable"})
    config = get_runtime_config()
    return {
        "status": "updated",
        "drift_detected": bool(config.get("drift_detected", False)),
        "forced_mode": config.get("forced_mode"),
        "drift_reason": config.get("drift_reason"),
        "drift_intensity": float(config.get("drift_intensity", 0.0) or 0.0),
        "drift_severity": config.get("drift_severity") or "stable",
    }
# ── Advanced Autonomy endpoints ───────────────────────────────────────────────

@router.get("/autonomy/explain")
async def autonomy_explain_decision():
    try:
        from autonomy.explainer import explain_decision, LAST_DECISION_CONTEXT
        result = explain_decision(LAST_DECISION_CONTEXT)
        if isinstance(result, dict) and "status" not in result:
            result = {"status": "ok", **result}
        return result
    except Exception as e:
        return {"status": "empty", "decision": None, "reasoning": [], "error": str(e)}

@router.post("/autonomy/simulate")
async def autonomy_simulate(request: Request):
    try:
        from autonomy.simulation_engine import simulate_failure_scenario
        data = await request.json()
        plan = data.get("plan", [])
        failure_rate = float(data.get("failure_rate", 0.20))
        context_key = data.get("context_key")
        results = simulate_failure_scenario(plan, failure_rate=failure_rate, context_key=context_key)
        passed = sum(1 for r in results if r.get("outcome") == "pass")
        failed = sum(1 for r in results if r.get("outcome") == "fail")
        return {
            "status": "ok",
            "steps": len(results),
            "passed": passed,
            "failed": failed,
            "failure_rate": failure_rate,
            "results": results,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@router.post("/autonomy/optimize")
async def autonomy_optimize_plan(request: Request):
    try:
        try:
            from andie_backend.interfaces.api.event_bus import emit_event
            from andie_backend.autonomy.plan_optimizer import (
                apply_replacements,
                prune_plan_with_reasons,
                resolve_min_trust_threshold,
                suggest_alternatives,
            )
        except ModuleNotFoundError:
            from interfaces.api.event_bus import emit_event
            from autonomy.plan_optimizer import (
                apply_replacements,
                prune_plan_with_reasons,
                resolve_min_trust_threshold,
                suggest_alternatives,
            )
        import uuid

        data = await request.json()
        plan = data.get("plan", [])
        candidate_skills = data.get("candidate_skills", [])
        profile = data.get("profile", "balanced")
        context_key = data.get("context_key")
        intent_type = data.get("intent_type")
        governance_profile = data.get("governance_profile") or profile
        portfolio_group = data.get("portfolio_group")
        execution_id = str(data.get("execution_id") or data.get("correlation_id") or uuid.uuid4())
        fallback_depth = max(1, int(data.get("fallback_depth", 3) or 3))
        context_match_min = max(0.0, min(float(data.get("context_match_min", 0.6) or 0.6), 1.0))
        min_trust = resolve_min_trust_threshold(profile)

        pruned_result = prune_plan_with_reasons(
            plan,
            context_key=context_key,
            min_trust_threshold=min_trust,
            profile=profile,
        )
        result = apply_replacements(
            pruned_result.get("kept", []),
            pruned_result.get("pruned", []),
            candidate_skills,
            context_key=context_key,
            profile=profile,
            fallback_depth=fallback_depth,
            context_match_min=context_match_min,
            intent_type=intent_type,
            governance_profile=governance_profile,
            portfolio_group=portfolio_group,
        )

        suggestions = []
        for step in plan:
            step_name = step.get("step") if isinstance(step, dict) else step
            step_suggestions = suggest_alternatives(
                str(step_name or ""),
                candidate_skills,
                context_key=context_key,
                top_k=fallback_depth,
                context_match_min=context_match_min,
                intent_type=intent_type,
                governance_profile=governance_profile,
                portfolio_group=portfolio_group,
            )
            suggestions.append({
                "step": step_name,
                "alternatives": step_suggestions,
            })
            for candidate in step_suggestions:
                await emit_event(
                    {
                        "type": candidate.get("outcome_weight_event"),
                        "execution_id": execution_id,
                        "timestamp": int(time.time() * 1000),
                        "step": step_name,
                        "candidate_skill": candidate.get("skill"),
                        "intent_type": intent_type,
                        "governance_profile": governance_profile,
                        "portfolio_group": portfolio_group,
                        "base_score": candidate.get("base_score"),
                        "outcome_weight_modifier": candidate.get("outcome_weight_modifier"),
                        "final_score": candidate.get("final_score"),
                    }
                )

        return {
            "status": "ok",
            "execution_id": execution_id,
            "profile": profile,
            "min_trust": min_trust,
            "intent_type": intent_type,
            "governance_profile": governance_profile,
            "portfolio_group": portfolio_group,
            "optimized": result,
            "suggestions": suggestions,
            "pruned": pruned_result.get("pruned", []),
            "kept": pruned_result.get("kept", []),
            "plan_stability": pruned_result.get("plan_stability"),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@router.get("/autonomy/alerts")
async def autonomy_alerts():
    try:
        from autonomy.observability_alerts import observability_alert_log_path
        import json
        log_path = observability_alert_log_path()
        alerts = []
        if log_path.exists():
            for line in log_path.read_text().strip().split("\n")[-20:]:
                try:
                    alerts.append(json.loads(line))
                except:
                    alerts.append({"raw": line})
        return {"alerts": alerts, "count": len(alerts), "log": str(log_path)}
    except Exception as e:
        return {"alerts": [], "count": 0, "error": str(e)}

@router.post("/autonomy/reason")
async def autonomy_reason(request: Request):
    try:
        from autonomy.reasoning_engine import ReasoningEngine, build_reasoning_plan
        import httpx, os
        data = await request.json()
        context = data.get("context", {})
        
        # Wire Ollama as the LLM callable
        async def ollama_call(prompt: str) -> str:
            try:
                res = await _ollama_chat(messages=[{"role": "user", "content": prompt}], model="tinyllama:latest")
                return res["response"]
            except Exception:
                return "reasoning unavailable"

        engine = ReasoningEngine(llm=lambda p: "plan: " + str(context.get("goals", [])))
        result = engine.process(context)
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Skills Trust + Simulate endpoints ────────────────────────────────────────

@router.get("/skills/trust")
async def skills_trust():
    try:
        from autonomy.trust_engine import compute_trust
        from autonomy.learning_engine import memory
        skills_data = memory.data if hasattr(memory, 'data') else {}
        skills = []
        for key, data in skills_data.items():
            skill_name = key.split("::")[0]
            trust = compute_trust(skill_name)
            fb = data.get("operator_feedback", {})
            skills.append({
                "name": skill_name,
                "trust": trust,
                "executions": data.get("executions", 0),
                "successes": data.get("successes", 0),
                "failures": data.get("failures", 0),
                "swaps_from": fb.get("swaps_from", 0),
                "skips": fb.get("skips", 0),
                "badge": "high" if trust >= 0.75 else "mid" if trust >= 0.45 else "low",
            })
        # Also add built skills
        import json
        from pathlib import Path
        skills_dir = Path("/media/jamai-jamison/78eb7352-2efe-465a-a250-c5df9c24726d/valhalla/skills")
        if skills_dir.exists():
            for d in skills_dir.iterdir():
                mp = d / "manifest.json"
                if mp.exists():
                    try:
                        manifest = json.loads(mp.read_text())
                        name = manifest.get("name", d.name)
                        if not any(s["name"] == name for s in skills):
                            trust = compute_trust(name)
                            skills.append({
                                "name": name,
                                "trust": trust,
                                "executions": 0,
                                "successes": 0,
                                "failures": 0,
                                "swaps_from": 0,
                                "skips": 0,
                                "badge": "high" if trust >= 0.75 else "mid" if trust >= 0.45 else "low",
                                "description": manifest.get("description", ""),
                            })
                    except: pass
        return {"skills": skills, "count": len(skills)}
    except Exception as e:
        return {"skills": [], "count": 0, "error": str(e)}

@router.post("/skills/plan")
async def skills_plan(request: Request):
    try:
        from skills import register_builtin_skills
        from skills.registry import registry
        from skills.router import build_execution_plan
        from andie_backend.autonomy.trust_engine import compute_trust
        from andie_backend.autonomy.learning_engine import skill_memory_snapshot
        from interfaces.api.skill_control import list_routable_skills, describe_suppressed_skills, blocked_primary_skill
        from andie_backend.autonomy.autonomy_profiles import DEFAULT_PROFILE

        data = await request.json()
        task = str(data.get("task") or "")

        register_builtin_skills()
        all_skills = registry.list()
        blocked = blocked_primary_skill(task, all_skills)
        routable_skills, _suppressed = list_routable_skills(all_skills)
        proposal = {"selectedSkill": None, "confidence": 0.0, "requiresApproval": False, "risk": None, "plan": []} if blocked else build_execution_plan(task, routable_skills)

        plan_steps = list(proposal.get("plan") or [])
        trust_values = [compute_trust(step_name) for step_name in plan_steps]
        trust_sum = sum(trust_values) or 1.0
        scored_plan = []
        for idx, step_name in enumerate(plan_steps):
            skill = registry.get(step_name)
            trust = trust_values[idx]
            snapshot = skill_memory_snapshot(step_name)
            scored_plan.append({
                "step": step_name,
                "normalized": round(trust / trust_sum, 6),
                "trust": trust,
                "risk": skill.risk_level if skill else "unknown",
                "requires_approval": bool(skill.requires_approval) if skill else False,
                "instability": bool(snapshot.get("unstable", False)),
                "failure_signatures": snapshot.get("failure_signatures") or {},
            })

        return {
            "plan": proposal,
            "scoredPlan": scored_plan,
            "profile": str(data.get("profile") or DEFAULT_PROFILE),
            "pruned": [],
            "planStability": round(sum(trust_values) / len(trust_values), 4) if trust_values else 0.0,
            "drift": {"detected": False, "intensity": 0.0, "severity": "stable"},
            "suppressedSkills": describe_suppressed_skills(all_skills),
        }
    except Exception as e:
        return {"plan": {"selectedSkill": None, "plan": []}, "scoredPlan": [], "error": str(e)}
@router.get("/skills/plan/snapshots")
async def skills_plan_snapshots():
    try:
        from interfaces.api.plan_store import list_plan_snapshots
        snapshots = list_plan_snapshots()
        return {"snapshots": snapshots, "count": len(snapshots)}
    except Exception as e:
        return {"snapshots": [], "count": 0, "error": str(e)}

@router.get("/skills/plan/snapshots/{filename}")
async def skills_plan_snapshot_by_name(filename: str):
    from interfaces.api.plan_store import load_plan_snapshot, load_latest_plan_snapshot
    if filename == "latest":
        latest = load_latest_plan_snapshot()
        if latest is None:
            raise HTTPException(status_code=404, detail="no snapshots")
        return {"snapshot": latest}
    data = load_plan_snapshot(filename)
    if data is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return {"snapshot": {"filename": filename, **data}}
@router.get("/skills/plan/snapshots/latest")
async def skills_plan_snapshot_latest():
    from interfaces.api.plan_store import load_latest_plan_snapshot
    latest = load_latest_plan_snapshot()
    if latest is None:
        raise HTTPException(status_code=404, detail="no snapshots")
    return {"snapshot": latest}
@router.post("/skills/plan/save")
async def skills_plan_save(request: Request):
    from interfaces.api.plan_store import save_plan_snapshot
    from autonomy.learning_engine import memory
    payload = await request.json()
    snapshot = save_plan_snapshot(
        name=str(payload.get("name") or "snapshot"),
        task=str(payload.get("task") or ""),
        editable_plan=payload.get("edited_plan") if isinstance(payload.get("edited_plan"), list) else [],
        edit_trail=payload.get("edit_trail") if isinstance(payload.get("edit_trail"), list) else [],
        actor=str(payload.get("actor") or "operator"),
        request_id=str(payload.get("request_id") or "") or None,
    )
    feedback_recorded = 0
    for event in snapshot.get("editTrail") or []:
        if not isinstance(event, dict):
            continue
        action = str(event.get("action") or "").strip().lower()
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        context_key = metadata.get("context_key")
        if action == "swap":
            from_skill = metadata.get("from")
            to_skill = metadata.get("to")
            if from_skill and to_skill:
                memory.log_operator_feedback("swap", from_skill=from_skill, to_skill=to_skill, context_key=context_key)
                feedback_recorded += 1
        elif action == "skip":
            skill_name = metadata.get("skill")
            if skill_name:
                memory.log_operator_feedback("skip", skill_name=skill_name, context_key=context_key)
                feedback_recorded += 1
    control_plane_metrics.increment("plan_snapshots_saved")
    return {"status": "saved", "snapshot": snapshot, "feedbackRecorded": feedback_recorded}
@router.post("/skills/plan/execute-edited")
async def skills_execute_plan(request: Request):
    try:
        from skills import register_builtin_skills
        from skills.executor import execute_skill
        from interfaces.api.outcome_tracking import record_skill_outcome_internal

        data = await request.json()
        edited_plan = data.get("edited_plan") if isinstance(data.get("edited_plan"), list) else []
        params = data.get("params") if isinstance(data.get("params"), dict) else {}

        register_builtin_skills()
        completed = []
        skipped = []

        for item in edited_plan:
            if not isinstance(item, dict):
                continue
            step_name = str(item.get("step") or "").strip()
            if not step_name:
                continue
            if bool(item.get("skipped")):
                skipped.append(step_name)
                continue

            execution = execute_skill(step_name, params)
            entry = {"step": step_name, "execution": execution}
            replaced_from = str(item.get("replacement_for") or "").strip() or None
            if replaced_from:
                entry["outcome"] = record_skill_outcome_internal(
                    skill_name=step_name,
                    result="success",
                    context_key=params.get("context_key"),
                    replaced_from=replaced_from,
                    latency=execution.get("latency"),
                    record_execution=False,
                )
            completed.append(entry)

        control_plane_metrics.increment("edited_plan_executions")
        control_plane_metrics.increment("plan_execute_total")
        return {"status": "done", "completed": completed, "skipped": skipped}
    except Exception as e:
        return {"completed": [], "skipped": [], "status": "error", "error": str(e)}
@router.post("/operator/override")
async def operator_override(request: Request):
    try:
        from autonomy.learning_engine import memory, score_skill
        data = await request.json()
        override_type = data.get("type", "override")
        from_skill = data.get("from_skill") or data.get("skill_name") or ""
        to_skill = data.get("to_skill", "")
        context_key = data.get("context_key")
        if override_type == "swap" and from_skill and to_skill:
            from_before = score_skill(from_skill, context_key=context_key)
            to_before = score_skill(to_skill, context_key=context_key)
            memory.log_operator_feedback("swap", from_skill=from_skill, to_skill=to_skill, context_key=context_key)
            from_after = score_skill(from_skill, context_key=context_key)
            to_after = score_skill(to_skill, context_key=context_key)
            return {"recorded": True, "type": "swap", "from": {"skill": from_skill, "previous_score": from_before, "updated_score": from_after}, "to": {"skill": to_skill, "previous_score": to_before, "updated_score": to_after}}
        if override_type == "skip" and from_skill:
            before = score_skill(from_skill, context_key=context_key)
            memory.log_operator_feedback("skip", skill_name=from_skill, context_key=context_key)
            after = score_skill(from_skill, context_key=context_key)
            return {"recorded": True, "type": "skip", "from": {"skill": from_skill, "previous_score": before, "updated_score": after}}
        return {"recorded": False, "type": override_type}
    except Exception as e:
        return {"status": "error", "error": str(e)}
@router.post("/skills/override")
async def skills_override(request: Request):
    return await operator_override(request)
@router.get("/skills/feedback")
async def skills_feedback():
    try:
        from autonomy.learning_engine import memory
        feedback = memory.get_feedback_summary() if hasattr(memory, "get_feedback_summary") else {}
        return {"feedback": feedback, "count": len(feedback), "total_skills": len(feedback)}
    except Exception as e:
        return {"feedback": {}, "count": 0, "total_skills": 0, "error": str(e)}

@router.post("/skills/propose")
async def skills_propose(request: Request):
    from skills import register_builtin_skills
    from skills.registry import registry
    from skills.router import build_execution_plan
    from interfaces.api.skill_control import list_routable_skills, describe_suppressed_skills
    data = await request.json()
    task = str(data.get("task") or "")
    register_builtin_skills()
    all_skills = registry.list()
    routable_skills, _ = list_routable_skills(all_skills)
    proposal = build_execution_plan(task, routable_skills)
    return {"proposal": proposal, "suppressedSkills": describe_suppressed_skills(all_skills)}

@router.get("/skills/tools")
async def skills_tools():
    from skills import register_builtin_skills
    from skills.registry import registry
    register_builtin_skills()
    tools = []
    for skill in registry.list():
        tools.append({
            "type": "function",
            "function": {
                "name": skill.name,
                "description": skill.description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            },
        })
    return {"tools": tools}

@router.post("/skills/execute")
async def skills_execute(request: Request):
    from skills import register_builtin_skills
    from skills.registry import registry
    from skills.executor import execute_skill
    from interfaces.api.skill_control import skill_suppression_reason
    from interfaces.api.outcome_tracking import record_skill_outcome_internal
    payload = await request.json()
    name = str(payload.get("skill") or payload.get("name") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if not name:
        raise HTTPException(status_code=400, detail="missing skill")
    register_builtin_skills()
    reason = skill_suppression_reason(name)
    if reason:
        return {"status": "blocked", "reason": reason}
    skill = registry.get(name)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")
    if skill.requires_approval:
        return {"status": "pending_approval", "requiresApproval": True, "stepMeta": {"requires_approval": True, "risk": skill.risk_level}}
    execution = execute_skill(name, params)
    response = {"status": "ok", "execution": execution}
    replaced_from = str(params.get("replaced_from") or "").strip() or None
    if replaced_from:
        response["outcome"] = record_skill_outcome_internal(
            skill_name=name,
            result="success",
            context_key=params.get("context_key"),
            replaced_from=replaced_from,
            latency=execution.get("latency"),
            record_execution=False,
        )
    return response

@router.post("/skills/execute-step")
async def skills_execute_step(request: Request):
    from skills import register_builtin_skills
    from skills.registry import registry
    from skills.executor import execute_skill
    from interfaces.api.skill_control import skill_suppression_reason
    from interfaces.api.outcome_tracking import record_skill_outcome_internal
    payload = await request.json()
    step = str(payload.get("step") or payload.get("skill") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if not step:
        raise HTTPException(status_code=400, detail="missing step")
    reason = str(payload.get("reason") or "").strip()
    if reason:
        control_plane_metrics.increment("plan_execute_rejected")
        return {"status": "rejected", "reason": reason}
    register_builtin_skills()
    blocked = skill_suppression_reason(step)
    if blocked:
        control_plane_metrics.increment("plan_execute_blocked")
        return {"status": "blocked", "reason": blocked}
    skill = registry.get(step)
    if skill is None:
        raise HTTPException(status_code=404, detail="step not found")
    approved = bool(payload.get("approved"))
    if skill.requires_approval and not approved:
        return {"status": "pending_approval", "stepMeta": {"requires_approval": True, "risk": skill.risk_level}}
    execution = execute_skill(step, params)
    response = {"status": "ok", "execution": execution}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    replaced_from = str(metadata.get("replaced_from") or params.get("replaced_from") or "").strip() or None
    if replaced_from:
        response["outcome"] = record_skill_outcome_internal(
            skill_name=step,
            result="success",
            context_key=params.get("context_key"),
            replaced_from=replaced_from,
            latency=execution.get("latency"),
            record_execution=False,
        )
    return response

@router.post("/skills/plan/execute")
async def skills_plan_execute(request: Request):
    from skills import register_builtin_skills
    from skills.registry import registry
    from skills.router import build_execution_plan
    from skills.executor import execute_skill_plan
    from andie_backend.autonomy.trust_engine import compute_trust
    from andie_backend.autonomy.learning_engine import skill_memory_snapshot
    from interfaces.api.skill_control import list_routable_skills
    from andie_backend.autonomy.runtime_config import get_runtime_config
    from interfaces.api.outcome_tracking import record_skill_outcome_internal
    payload = await request.json()
    task = str(payload.get("task") or "")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    register_builtin_skills()
    all_skills = registry.list()
    routable_skills, _ = list_routable_skills(all_skills)
    proposal = build_execution_plan(task, routable_skills)
    steps = list(proposal.get("plan") or [])
    if not steps:
        return {"status": "blocked", "execution": {"completed": [], "blockedOn": None, "remaining": []}, "scoredPlan": []}
    scored = []
    trusts = [compute_trust(name) for name in steps]
    denom = sum(trusts) or 1.0
    for i, name in enumerate(steps):
        skill = registry.get(name)
        snap = skill_memory_snapshot(name)
        scored.append({"step": name, "normalized": round(trusts[i] / denom, 6), "trust": trusts[i], "risk": skill.risk_level if skill else "unknown", "requires_approval": bool(skill.requires_approval) if skill else False, "instability": bool(snap.get("unstable", False)), "failure_signatures": snap.get("failure_signatures") or {}})
    execution = execute_skill_plan(steps, params)
    control_plane_metrics.increment("plan_execute_total")
    if execution.get("status") == "pending_approval":
        control_plane_metrics.increment("plan_execute_blocked")
        return {"status": "pending_approval", "execution": execution, "scoredPlan": scored, "drift": {"detected": False, "intensity": 0.0, "severity": "stable"}, "replacementOutcomes": {"success": 0, "failure": 0, "total": 0}}
    runtime = get_runtime_config()
    outcomes_enabled = bool(runtime.get("runtime_outcome_emission_enabled", True))
    replacement_map = params.get("replacement_map") if isinstance(params.get("replacement_map"), dict) else {}
    replacement = {"success": 0, "failure": 0, "total": 0}
    if not outcomes_enabled:
        replacement["disabled"] = True
    completed = []
    for entry in execution.get("completed") or []:
        item = dict(entry) if isinstance(entry, dict) else {"skill": str(entry)}
        skill_name = str(item.get("skill") or "").strip()
        replaced_from = str(replacement_map.get(skill_name) or "").strip() or None
        if outcomes_enabled and replaced_from and skill_name:
            outcome = record_skill_outcome_internal(skill_name=skill_name, result="success", context_key=params.get("context_key"), replaced_from=replaced_from, latency=item.get("latency"), record_execution=False)
            item["outcome"] = outcome
            replacement["success"] += 1
            replacement["total"] += 1
        completed.append(item)
    execution["completed"] = completed
    control_plane_metrics.increment("plan_execute_auto")
    return {"status": "ok", "execution": execution, "scoredPlan": scored, "drift": {"detected": False, "intensity": 0.0, "severity": "stable"}, "replacementOutcomes": replacement}

@router.get("/skills/learning")
async def skills_learning():
    from autonomy.learning_engine import memory
    entries = []
    names = set()
    for key, value in (memory.data or {}).items():
        if str(key).startswith("__") or not isinstance(value, dict):
            continue
        skill_name = value.get("skill") or str(key).split("::")[0]
        names.add(skill_name)
        entries.append({"key": key, "skill": skill_name, "context_key": value.get("context_key"), "executions": int(value.get("executions", 0) or 0), "successes": int(value.get("successes", 0) or 0), "failures": int(value.get("failures", 0) or 0)})
    return {"skills": sorted(names), "entries": entries, "memoryPath": str(memory.path)}

@router.post("/skills/outcome")
async def skills_outcome_ingest(request: Request):
    from interfaces.api.outcome_tracking import record_skill_outcome_internal
    payload = await request.json()
    return record_skill_outcome_internal(
        skill_name=str(payload.get("skill") or ""),
        result=str(payload.get("result") or "success"),
        context_key=payload.get("context_key"),
        replaced_from=payload.get("replaced_from"),
        latency=payload.get("latency"),
        error=payload.get("error"),
        source=str(payload.get("source") or "live"),
    )

@router.get("/skills/control")
async def skills_control_get():
    from interfaces.api.skill_control import get_skill_control_state
    return {"controlState": get_skill_control_state()}

@router.put("/skills/control")
async def skills_control_put(request: Request):
    from interfaces.api.skill_control import update_skill_control_state
    payload = await request.json()
    try:
        state = update_skill_control_state(
            incident_mode=payload.get("incident_mode"),
            blacklisted_skills=payload.get("blacklisted_skills"),
            updated_by=str(payload.get("actor") or "operator-ui"),
            reason=payload.get("reason"),
            request_id=payload.get("request_id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "saved", "controlState": state}

@router.post("/skills/plan/optimize")
async def skills_plan_optimize(request: Request):
    from skills import register_builtin_skills
    from skills.registry import registry
    from autonomy.plan_optimizer import prune_plan_with_reasons, apply_replacements, resolve_min_trust_threshold
    from autonomy.runtime_config import get_runtime_config, update_runtime_config
    payload = await request.json()
    plan = payload.get("plan") if isinstance(payload.get("plan"), list) else []
    profile = str(payload.get("profile") or "balanced")
    threshold = payload.get("min_trust_threshold")
    resolved_threshold = resolve_min_trust_threshold(profile=profile, override=threshold)
    pruned = prune_plan_with_reasons(plan, context_key=payload.get("context_key"), min_trust_threshold=resolved_threshold, profile=profile, global_mode=str(payload.get("global_mode") or "assisted"))
    register_builtin_skills()
    candidates = [{"name": s.name, "risk": s.risk_level, "requires_approval": s.requires_approval, "depends_on": list(s.depends_on or []), "keywords": list(s.keywords or []), "context_tags": []} for s in registry.list()]
    replaced = apply_replacements(pruned.get("kept") or [], pruned.get("pruned") or [], candidates, context_key=payload.get("context_key"), profile=profile, global_mode=str(payload.get("global_mode") or "assisted"))
    metrics = control_plane_metrics.snapshot()
    total = float(metrics.get("plan_execute_total", 0) or 0)
    failed = float(metrics.get("plan_execute_failed", 0) or 0)
    drift_ratio = (failed / total) if total > 0 else 0.0
    drift_intensity = max(0.0, min(drift_ratio * 2.0, 1.0))
    drift_detected = drift_intensity >= 0.5
    severity = "severe" if drift_intensity >= 0.75 else "moderate" if drift_detected else "stable"
    reason = "failure_rate_spike" if drift_detected else None
    config_before = get_runtime_config()
    recovered = bool(config_before.get("drift_detected", False)) and not drift_detected
    update_runtime_config({"drift_detected": drift_detected, "drift_intensity": drift_intensity, "drift_severity": severity, "drift_reason": reason, "forced_mode": "manual" if drift_detected else None})
    pruned_steps = replaced.get("pruned") or []
    control_plane_metrics.increment("pruned_step_count", len(pruned_steps))
    control_plane_metrics.increment("pruned_predicted_failures", sum(float(item.get("failure_probability", 0.0) or 0.0) for item in pruned_steps))
    return {
        "plan": replaced.get("kept") or [],
        "kept": replaced.get("kept") or [],
        "avoided": replaced.get("avoided") or [],
        "replaced": replaced.get("replaced") or [],
        "pruned": pruned_steps,
        "inputSteps": len(plan),
        "outputSteps": len(replaced.get("kept") or []),
        "minTrustThreshold": resolved_threshold,
        "drift": {"detected": drift_detected, "intensity": drift_intensity, "severity": severity, "forcedMode": "manual" if drift_detected else None, "reason": reason, "recovered": recovered},
    }

@router.post("/skills/simulate")
async def skills_simulate(request: Request):
    import tempfile
    from autonomy.simulation_engine import simulate_with_feedback
    from autonomy.memory_store import MemoryStore
    payload = await request.json()
    plan = payload.get("plan") if isinstance(payload.get("plan"), list) else []
    temp_memory = MemoryStore(path=tempfile.NamedTemporaryFile(prefix="andie-sim-", suffix=".json", delete=False).name)
    simulation = simulate_with_feedback(
        plan,
        failure_rate=float(payload.get("failure_rate", 0.2) or 0.2),
        seed=payload.get("seed"),
        apply_feedback=bool(payload.get("apply_feedback", False)),
        context_key=payload.get("context_key"),
        memory_store=temp_memory,
        predictive=bool(payload.get("predictive", True)),
    )
    control_plane_metrics.increment("simulation_runs")
    return {"status": "ok", "isolated": True, "simulation": simulation}

@router.get("/metrics/control-plane")
async def metrics_control_plane():
    return control_plane_metrics.to_dict()

@router.get("/trust/dashboard")
async def trust_dashboard():
    counters = control_plane_metrics.snapshot()
    total = int(counters.get("outcome_events_total", 0) or 0)
    real = int(counters.get("real_outcome_events_total", 0) or 0)
    synthetic = max(0, total - real)
    real_ratio = (real / total) if total else 0.0
    tier = "high" if real_ratio >= 0.75 else "medium" if real_ratio >= 0.4 else "low"
    return {"confidence_tier": tier, "real_vs_synthetic": {"total": total, "real": real, "synthetic": synthetic}}
# ── Artifact browser endpoints ────────────────────────────────────────────────
from pathlib import Path as _APath
import mimetypes as _mimetypes

_ARTIFACTS_ROOT = _APath("/app/workspace/artifacts")


@router.get("/artifacts")
async def list_artifacts():
    """List all artifact builds. Returns metadata from ANDIE_BUILD.json if present."""
    if not _ARTIFACTS_ROOT.exists():
        return {"artifacts": []}
    results = []
    for job_dir in sorted(_ARTIFACTS_ROOT.iterdir(), reverse=True):
        if not job_dir.is_dir():
            continue
        meta_path = job_dir / "ANDIE_BUILD.json"
        if meta_path.exists():
            try:
                import json as _jj
                meta = _jj.loads(meta_path.read_text())
            except Exception:
                meta = {}
        else:
            meta = {}
        files = [f.relative_to(job_dir).as_posix() for f in job_dir.rglob("*") if f.is_file() and f.name != "ANDIE_BUILD.json"]
        results.append({
            "job_id": job_dir.name,
            "project": meta.get("project", job_dir.name),
            "target": meta.get("target", "unknown"),
            "built_at": meta.get("built_at", ""),
            "files": files,
        })
    return {"artifacts": results}


@router.get("/artifacts/{job_id}/file/{filepath:path}")
async def get_artifact_file(job_id: str, filepath: str):
    """Serve a single artifact file as plain text."""
    from fastapi.responses import PlainTextResponse, Response
    # Sanitize — prevent path traversal
    job_dir = _ARTIFACTS_ROOT / job_id
    target = (job_dir / filepath).resolve()
    if not str(target).startswith(str(job_dir.resolve())):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists() or not target.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="File not found")
    content = target.read_text(errors="replace")
    mime, _ = _mimetypes.guess_type(str(target))
    return Response(content=content, media_type=mime or "text/plain")


# ── Diagnostics endpoints ─────────────────────────────────────────────────────
from andie_backend.andie.diagnostics.probe_runner import run_domain as _run_domain, run_all as _run_all_diag, ALL_DOMAINS as _ALL_DOMAINS


@router.get("/diagnostics/run")
async def diagnostics_run_all():
    """Run all diagnostic domains in parallel and return structured health report."""
    return await _run_all_diag()


@router.get("/diagnostics/run/{domain}")
async def diagnostics_run_domain(domain: str):
    """Run diagnostic probes for a single domain (containers/network/gpu/storage/models)."""
    if domain not in _ALL_DOMAINS:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Unknown domain '{domain}'. Valid: {_ALL_DOMAINS}")
    return await _run_domain(domain)


# ── Runtime Registry endpoint ─────────────────────────────────────────────────
from andie_backend.andie.trainstation.registry import snapshot as _registry_snapshot
from andie_backend.andie.trainstation.healthchecks import start_background_poll as _start_poll, check_all as _check_all


@router.on_event("startup")
async def _trainstation_startup():
    """On backend startup: run initial health check then start background poll."""
    try:
        await _check_all()
        _start_poll(interval=30)
    except Exception:
        pass


@router.get("/registry")
async def get_registry():
    """Live service registry — status of all stack components."""
    return _registry_snapshot()


@router.post("/registry/refresh")
async def refresh_registry():
    """Trigger immediate health check sweep across all services."""
    results = await _check_all()
    return {"refreshed": True, "results": results, "registry": _registry_snapshot()}


# ── UI Integrity Endpoints ────────────────────────────────────────────────────
from andie_backend.andie.trainstation.ui_health import run_checks as _ui_checks
from andie_backend.andie.trainstation.ui_recovery import recover as _ui_recover, rebuild_assets as _ui_rebuild


@router.get("/ui/health")
async def ui_health():
    """Full frontend visibility health sweep with composite visibility_score."""
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _ui_checks)
    return result


@router.post("/ui/recover")
async def ui_recover(max_actions: int = 2):
    """
    Run UI recovery pipeline.
    max_actions: 1=restart_only, 2=restart+rebuild (default), 3=+node_modules, 4=+rollback
    """
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _ui_recover(max_actions=max_actions))
    return result


@router.post("/ui/rebuild")
async def ui_rebuild():
    """Trigger Vite asset rebuild inside the andie-ui container."""
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _ui_rebuild)
    return result



# ── Observation loop endpoints ────────────────────────────────────────────────
from andie_backend.andie.media.router import router as _media_router
router.include_router(_media_router)

from andie_backend.andie.audio.router import router as _audio_router
router.include_router(_audio_router)
from andie_backend.andie.observation import loop as _obs_loop
from andie_backend.andie.observation.diagnosis import diagnose as _diagnose


@router.on_event("startup")
async def _observation_loop_startup():
    """Start the continuous background observation loop on backend startup."""
    try:
        _obs_loop.start(interval=30)
    except Exception:
        pass


@router.get("/observe/status")
async def observe_status():
    """Latest cached observation snapshot across all domains."""
    snap = _obs_loop.get_latest()
    if snap is None:
        from andie_backend.andie.diagnostics.probe_runner import run_all
        result = await run_all()
        return result
    return {
        "overall": snap.overall,
        "wall_time": snap.wall_time,
        "domains": {
            name: {
                "status": ds.status,
                "checks": ds.checks,
                "elapsed_ms": ds.elapsed_ms,
            }
            for name, ds in snap.domains.items()
        },
    }


@router.get("/observe/diagnose")
async def observe_diagnose():
    """Correlated diagnosis with causes and recommended actions."""
    snap = _obs_loop.get_latest()
    if snap is None:
        from andie_backend.andie.diagnostics.probe_runner import run_all
        import time, datetime
        result = await run_all()
        from andie_backend.andie.observation.loop import ObservationSnapshot, DomainSnapshot
        now = time.monotonic()
        wall = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        domains = {
            d: DomainSnapshot(domain=d, status=v.get("status","unknown"),
                              checks=v.get("checks",[]), elapsed_ms=v.get("elapsed_ms",0),
                              captured_at=now)
            for d, v in result.get("domains", {}).items()
        }
        snap = ObservationSnapshot(overall=result.get("status","unknown"),
                                   domains=domains, captured_at=now, wall_time=wall)
    return _diagnose(snap)


@router.get("/observe/history")
async def observe_history(n: int = 20):
    """Last N observation snapshots (ring buffer, max 60)."""
    snaps = _obs_loop.get_history(n)
    return {
        "count": len(snaps),
        "snapshots": [
            {
                "overall": s.overall,
                "wall_time": s.wall_time,
                "domain_statuses": {name: ds.status for name, ds in s.domains.items()},
            }
            for s in snaps
        ],
    }

# --- Valhalla Workspace Snapshot + Replay + Event Stream ---
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_or_now(value: Any) -> str:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).isoformat()
        except Exception:
            return value
    return _now_iso()


def _to_status_value(task: Any) -> str:
    status = getattr(task, "status", None)
    if status is None:
        return "unknown"
    value = getattr(status, "value", None)
    return str(value or status)


def _build_runs(request: Request, limit: int = 50) -> List[Dict[str, Any]]:
    queue = getattr(request.app.state, "task_queue", None)
    runtime_state = getattr(request.app.state, "runtime_state", None)
    runtime_snapshot = runtime_state.snapshot() if runtime_state else {}

    runs: List[Dict[str, Any]] = []
    if queue is not None and hasattr(queue, "get_all_tasks"):
        try:
            all_tasks = queue.get_all_tasks() or []
            all_tasks = sorted(
                all_tasks,
                key=lambda t: _parse_iso_or_now(getattr(t, "created_at", None)),
                reverse=True,
            )
            for task in all_tasks[: max(1, int(limit))]:
                execution_id = str(getattr(task, "task_id", ""))
                status_value = _to_status_value(task)
                retries = int(getattr(task, "retry_count", 0) or 0)
                failed = status_value in {"failed", "dead_letter", "error"}
                completed = status_value in {"completed", "done", "verified"}
                confidence = 0.92 if completed else 0.78
                if failed:
                    confidence = 0.28
                confidence = max(0.05, confidence - min(retries * 0.1, 0.4))

                runs.append(
                    {
                        "execution_id": execution_id,
                        "adapter_id": str(getattr(task, "claimed_by_worker", None) or "orchestrator.worker"),
                        "capability_id": str(getattr(task, "task_type", None) or "orchestration.task"),
                        "policy_profile": str(runtime_snapshot.get("governance", {}).get("active_profile", "prod")),
                        "lifecycle_state": status_value,
                        "confidence_score": round(confidence, 3),
                        "confidence_trend": "falling" if failed else "stable",
                        "rollback_triggered": bool(failed and retries > 0),
                        "rollback_outcome": "triggered" if (failed and retries > 0) else "",
                        "verification_result": "failed" if failed else ("passed" if completed else "pending"),
                        "started_at": _parse_iso_or_now(getattr(task, "created_at", None)),
                        "timestamp": _parse_iso_or_now(getattr(task, "created_at", None)),
                    }
                )
        except Exception:
            runs = []

    return runs


def _build_live_events(limit: int = 120) -> List[Dict[str, Any]]:
    events = recent_events(max(1, int(limit)))
    normalized: List[Dict[str, Any]] = []
    for item in events:
        execution_id = item.get("execution_id") or item.get("task_id") or item.get("correlation_id") or ""
        to_state = item.get("to_state") or item.get("status") or item.get("event") or item.get("type") or "unknown"
        reason = item.get("reason") or item.get("message") or item.get("event") or item.get("type") or ""
        normalized.append(
            {
                "timestamp": _parse_iso_or_now(item.get("emitted_at") or item.get("timestamp")),
                "execution_id": str(execution_id),
                "adapter_id": str(item.get("adapter_id") or item.get("previous_worker") or item.get("claimed_by_worker") or "orchestrator.worker"),
                "to_state": str(to_state),
                "reason": str(reason),
                "type": str(item.get("type") or to_state),
                "base_score": item.get("base_score"),
                "outcome_weight_modifier": item.get("outcome_weight_modifier"),
                "final_score": item.get("final_score"),
                "candidate_skill": item.get("candidate_skill"),
                "intent_type": item.get("intent_type"),
                "governance_profile": item.get("governance_profile"),
                "portfolio_group": item.get("portfolio_group"),
                "window": item.get("window"),
                "sample_count": item.get("sample_count"),
                "previous_average": item.get("previous_average"),
                "current_average": item.get("current_average"),
                "trend": item.get("trend"),
                "delta": item.get("delta"),
                "removed_samples": item.get("removed_samples"),
            }
        )
    return normalized[-max(1, int(limit)):]


def _build_replay_index(runs: List[Dict[str, Any]], live_events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped = defaultdict(int)
    for event in live_events:
        execution_id = event.get("execution_id")
        if execution_id:
            grouped[str(execution_id)] += 1

    replay_index: Dict[str, Dict[str, Any]] = {}
    for run in runs:
        execution_id = run.get("execution_id") or ""
        replay_index[execution_id] = {
            "replay_available": grouped.get(execution_id, 0) > 0,
            "events": grouped.get(execution_id, 0),
        }
    return replay_index


def _build_telemetry(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not runs:
        return {
            "risk_indicators": {
                "rollback_frequency": 0.0,
                "confidence_decay_rate": 0.0,
                "telemetry_volatility_index": 0.0,
            },
            "by_confidence_trend": {},
            "confidence_bands": {"low": 0, "moderate": 0, "high": 0},
        }

    rollback_frequency = sum(1 for run in runs if run.get("rollback_triggered")) / len(runs)
    low_conf = sum(1 for run in runs if float(run.get("confidence_score", 0.0)) < 0.4)
    high_conf = sum(1 for run in runs if float(run.get("confidence_score", 0.0)) >= 0.75)
    moderate_conf = len(runs) - low_conf - high_conf

    trend_counts: Dict[str, int] = defaultdict(int)
    for run in runs:
        trend_counts[str(run.get("confidence_trend", "stable"))] += 1

    confidence_volatility = (low_conf / len(runs)) if runs else 0.0
    return {
        "risk_indicators": {
            "rollback_frequency": round(rollback_frequency, 3),
            "confidence_decay_rate": round(confidence_volatility, 3),
            "telemetry_volatility_index": round((rollback_frequency + confidence_volatility) / 2.0, 3),
        },
        "by_confidence_trend": dict(trend_counts),
        "confidence_bands": {
            "low": low_conf,
            "moderate": moderate_conf,
            "high": high_conf,
        },
    }


def _build_trust(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[str(run.get("adapter_id", "orchestrator.worker"))].append(run)

    rankings = []
    for adapter_id, adapter_runs in grouped.items():
        total = len(adapter_runs)
        passed = sum(1 for run in adapter_runs if run.get("verification_result") == "passed")
        reliability = (passed / total) if total else 0.0
        gov_effectiveness = 1.0 - (sum(1 for run in adapter_runs if run.get("rollback_triggered")) / total if total else 0.0)
        rankings.append(
            {
                "adapter_id": adapter_id,
                "reliability_score": round(reliability, 3),
                "governance_effectiveness_score": round(gov_effectiveness, 3),
                "autonomy_eligible": reliability >= 0.75 and gov_effectiveness >= 0.7,
            }
        )

    rankings.sort(key=lambda row: (row["reliability_score"], row["governance_effectiveness_score"]), reverse=True)
    watchlist = [row for row in rankings if row["reliability_score"] < 0.65]

    avg_rel = sum(row["reliability_score"] for row in rankings) / len(rankings) if rankings else 0.0
    avg_gov = sum(row["governance_effectiveness_score"] for row in rankings) / len(rankings) if rankings else 0.0

    return {
        "adapter_rankings": rankings,
        "top_adapters": rankings[:3],
        "watchlist_adapters": watchlist,
        "average_reliability_score": round(avg_rel, 3),
        "average_governance_effectiveness": round(avg_gov, 3),
        "autonomy_readiness": {
            "ready": sum(1 for row in rankings if row["autonomy_eligible"]),
            "supervised": sum(1 for row in rankings if (not row["autonomy_eligible"] and row["reliability_score"] >= 0.5)),
            "constrained": sum(1 for row in rankings if row["reliability_score"] < 0.5),
        },
    }


def _build_governance(profile: str, telemetry: Dict[str, Any], trust: Dict[str, Any]) -> Dict[str, Any]:
    risk = telemetry.get("risk_indicators", {})
    rollback_frequency = float(risk.get("rollback_frequency", 0.0) or 0.0)
    volatility = float(risk.get("telemetry_volatility_index", 0.0) or 0.0)

    suggestions: List[Dict[str, Any]] = []
    if rollback_frequency >= 0.2:
        suggestions.append(
            {
                "id": "tighten_on_rollback_frequency",
                "severity": "high",
                "signal": "rollback frequency exceeded threshold",
                "recommended_change": {"governance": "tighten", "rollback_frequency_threshold": 0.2},
                "reason": f"rollback_frequency={rollback_frequency:.3f}",
                "profile": profile,
            }
        )
    if volatility >= 0.2:
        suggestions.append(
            {
                "id": "increase_supervision_on_volatility",
                "severity": "advisory",
                "signal": "telemetry volatility is elevated",
                "recommended_change": {"governance": "watch", "supervision": "increase"},
                "reason": f"telemetry_volatility_index={volatility:.3f}",
                "profile": profile,
            }
        )

    if not suggestions:
        suggestions.append(
            {
                "id": "retain_current_governance_posture",
                "severity": "none",
                "signal": "balanced trust profile",
                "recommended_change": {"governance": "maintain"},
                "reason": "current risk indicators do not justify additional tightening or relaxation",
                "profile": profile,
            }
        )

    watchlist_count = len(trust.get("watchlist_adapters", []))
    requires_tightening = any(item.get("severity") == "high" for item in suggestions)

    projections = {}
    for projected in ("dev", "staging", "prod"):
        projections[projected] = {
            "profile": projected,
            "suggestions": suggestions,
            "watchlist_count": watchlist_count,
            "requires_immediate_tightening": requires_tightening if projected == "prod" else False,
            "thresholds": {
                "rollback_frequency": 0.45 if projected == "dev" else (0.3 if projected == "staging" else 0.2),
                "telemetry_volatility_index": 0.5 if projected == "dev" else (0.35 if projected == "staging" else 0.25),
                "confidence_decay_rate": 0.45 if projected == "dev" else (0.3 if projected == "staging" else 0.2),
                "watchlist_reliability": 0.5 if projected == "dev" else (0.6 if projected == "staging" else 0.7),
                "relax_average_reliability": 0.8 if projected == "dev" else (0.85 if projected == "staging" else 0.9),
                "relax_risk": 0.2 if projected == "dev" else (0.12 if projected == "staging" else 0.08),
            },
        }

    overlay_patch = {
        "profile": profile,
        "patch_format_version": "1.0",
        "generated_at": _now_iso(),
        "source": {
            "risk_indicators": risk,
            "average_reliability_score": trust.get("average_reliability_score", 0.0),
            "watchlist_count": watchlist_count,
            "requires_immediate_tightening": requires_tightening,
        },
        "recommended_changes": [
            {
                "action": "no_change" if item.get("id") == "retain_current_governance_posture" else "tighten_threshold",
                "target": "policy",
                "patch": item.get("recommended_change", {}),
                "reason": item.get("reason", ""),
                "severity": item.get("severity", "none"),
            }
            for item in suggestions
        ],
    }

    return {
        "active": {
            "profile": profile,
            "suggestions": suggestions,
            "watchlist_count": watchlist_count,
            "requires_immediate_tightening": requires_tightening,
            "thresholds": projections[profile]["thresholds"],
        },
        "projections": projections,
        "overlay_patch_candidate": overlay_patch,
    }


def _pressure_tier_from_snapshot(runtime_snapshot: Dict[str, Any]) -> str:
    status_bar = runtime_snapshot.get("status_bar") or {}
    telemetry = runtime_snapshot.get("telemetry_center") or {}
    indicators = telemetry.get("risk_indicators") or {}

    watchlist = max(0, int(status_bar.get("watchlist_count") or 0))
    rollback = float(indicators.get("rollback_frequency") or 0.0)
    confidence_decay = float(indicators.get("confidence_decay_rate") or 0.0)

    pressure = min(1.0, (0.03 * watchlist) + (0.5 * rollback) + (0.5 * confidence_decay))
    if pressure >= 0.85:
        return "critical"
    if pressure >= 0.65:
        return "high"
    if pressure >= 0.35:
        return "elevated"
    return "baseline"


def _build_workspace_snapshot(request: Request, limit: int = 50) -> Dict[str, Any]:
    profile = "prod"
    runs = _build_runs(request, limit=limit)
    live_events = _build_live_events(limit=120)
    telemetry = _build_telemetry(runs)
    trust = _build_trust(runs)
    governance = _build_governance(profile, telemetry, trust)
    replay_index = _build_replay_index(runs, live_events)

    return {
        "generated_at": _now_iso(),
        "status_bar": {
            "active_profile": profile,
            "requires_immediate_tightening": governance.get("active", {}).get("requires_immediate_tightening", False),
            "watchlist_count": governance.get("active", {}).get("watchlist_count", 0),
            "total_records": len(runs),
        },
        "navigation": {
            "sections": ["runs", "replay", "telemetry", "governance", "trust", "chat"],
        },
        "runs": runs,
        "replay_index": replay_index,
        "telemetry_center": telemetry,
        "governance_center": governance,
        "trust_center": trust,
        "live_event_stream": live_events[-8:],
    }


@router.get("/workspace-snapshot")
async def workspace_snapshot(request: Request, limit: int = 50):
    return _build_workspace_snapshot(request, limit=limit)


@router.get("/api/workspace-snapshot")
async def workspace_snapshot_api_alias(request: Request, limit: int = 50):
    return _build_workspace_snapshot(request, limit=limit)


def _filter_replay_events(execution_id: str, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    execution_id = str(execution_id)
    matched = []
    for event in events:
        if str(event.get("execution_id", "")) == execution_id:
            matched.append(event)
    return matched


@router.get("/replay/{execution_id}")
async def replay_execution(execution_id: str, request: Request):
    events = _build_live_events(limit=500)
    matched = _filter_replay_events(execution_id, events)

    queue = getattr(request.app.state, "task_queue", None)
    queue_task = None
    if queue is not None and hasattr(queue, "get_task"):
        try:
            queue_task = queue.get_task(execution_id)
        except Exception:
            queue_task = None

    timeline = [
        {
            "timestamp": item.get("timestamp"),
            "state": item.get("to_state"),
            "reason": item.get("reason"),
        }
        for item in matched
    ]

    return {
        "execution_id": execution_id,
        "found": bool(matched or queue_task),
        "events": matched,
        "timeline": timeline,
        "summary": {
            "event_count": len(matched),
            "first_event_at": matched[0]["timestamp"] if matched else None,
            "last_event_at": matched[-1]["timestamp"] if matched else None,
        },
    }


@router.get("/api/replay/{execution_id}")
async def replay_execution_api_alias(execution_id: str, request: Request):
    return await replay_execution(execution_id, request)


@router.websocket("/workspace-events/ws")
async def workspace_events_ws(websocket: WebSocket):
    await websocket_endpoint(websocket)


@router.websocket("/api/workspace-events/ws")
async def workspace_events_ws_api_alias(websocket: WebSocket):
    await websocket_endpoint(websocket)


def _get_executive_controller(request: Request):
    controller = getattr(request.app.state, "executive_controller", None)
    if controller is not None:
        return controller

    try:
        from andie_backend.executive.controller import ExecutiveController
        from andie_backend.executive.models import ExecutiveConfig
    except ModuleNotFoundError:
        from executive.controller import ExecutiveController
        from executive.models import ExecutiveConfig

    store_path = os.environ.get("ANDIE_EXECUTIVE_STATE_PATH", "storage/executive/executive_state.json")
    simulate = os.environ.get("ANDIE_EXECUTIVE_SIMULATE", "1").strip().lower() not in {"0", "false", "no"}
    controller = ExecutiveController(
        config=ExecutiveConfig(
            store_path=store_path,
            simulate_execution=simulate,
        )
    )
    request.app.state.executive_controller = controller
    return controller


def _get_bounded_scheduler(request: Request):
    scheduler = getattr(request.app.state, 'bounded_scheduler', None)
    if scheduler is not None:
        return scheduler

    controller = _get_executive_controller(request)
    try:
        from andie_backend.executive.bounded_scheduler import BoundedScheduler
    except ModuleNotFoundError:
        from executive.bounded_scheduler import BoundedScheduler

    interval_seconds = int(os.environ.get('ANDIE_SCHEDULER_INTERVAL_SECONDS', '60'))
    scheduler = BoundedScheduler(controller, interval_seconds=max(1, interval_seconds))
    request.app.state.bounded_scheduler = scheduler
    return scheduler


def _get_a2a_router(request: Request):
    router_instance = getattr(request.app.state, 'a2a_router', None)
    if router_instance is not None:
        return router_instance

    controller = _get_executive_controller(request)
    try:
        from andie_backend.executive.a2a import LocalA2ARouter
    except ModuleNotFoundError:
        from executive.a2a import LocalA2ARouter

    router_instance = LocalA2ARouter(controller)
    request.app.state.a2a_router = router_instance
    return router_instance


@router.get("/executive/agenda")
async def executive_agenda(request: Request):
    controller = _get_executive_controller(request)
    agenda = controller.store.get_executive_agenda()
    if agenda is None:
        agenda = controller._refresh_executive_agenda()
    agenda_dict = agenda.to_dict()
    priorities = list(agenda_dict.get('priorities') or [])
    active_priority = priorities[0]['priority_id'] if priorities else None
    budget_status = dict(agenda_dict.get('budget_status') or {})
    summary = {
        'active_priority': active_priority,
        'blocked_count': int(budget_status.get('blocked_count', len(agenda_dict.get('blockers') or []))),
        'deferred_count': int(budget_status.get('deferred_count', 0)),
        'active_count': int(budget_status.get('active_count', 0)),
        'budget_status': str(budget_status.get('health', 'unknown')),
    }
    return {"status": "ok", "agenda": agenda_dict, "summary": summary}


@router.get("/executive/agenda/decisions")
async def executive_agenda_decisions(request: Request, limit: int = 50):
    controller = _get_executive_controller(request)
    normalized_limit = max(1, min(int(limit), 500))
    decisions = controller.store.list_agenda_decisions()
    selected = decisions[-normalized_limit:]
    return {
        "status": "ok",
        "count": len(selected),
        "items": [item.to_dict() for item in reversed(selected)],
    }


@router.get("/executive/agenda/decisions/{decision_id}")
async def executive_agenda_decision_by_id(decision_id: str, request: Request):
    controller = _get_executive_controller(request)
    for item in reversed(controller.store.list_agenda_decisions()):
        if item.decision_id == decision_id:
            return {"status": "ok", "decision": item.to_dict()}
    raise HTTPException(status_code=404, detail="agenda decision not found")


@router.get("/executive/agenda/explain")
async def executive_agenda_explain(request: Request):
    controller = _get_executive_controller(request)
    agenda = controller.store.get_executive_agenda()
    if agenda is None:
        agenda = controller._refresh_executive_agenda()

    agenda_dict = agenda.to_dict()
    priorities = list(agenda_dict.get('priorities') or [])
    active_priority = priorities[0] if priorities else None

    decision = None
    for item in reversed(controller.store.list_agenda_decisions()):
        if active_priority is None or item.selected_priority == active_priority.get('priority_id'):
            decision = item
            break

    return {
        'status': 'ok',
        'active_priority': active_priority,
        'rationale': (decision.rationale if decision else 'no_decision_recorded'),
        'identity_checks': (decision.identity_checks if decision else []),
        'governance_checks': (decision.governance_checks if decision else []),
        'policy': controller.get_agenda_policy(),
    }


@router.get("/executive/agenda/replay")
async def executive_agenda_replay(request: Request, cycle: int | None = None):
    controller = _get_executive_controller(request)
    decisions = controller.store.list_agenda_decisions()

    if cycle is None:
        cutoff = len(decisions)
    else:
        if cycle < 1 or cycle > len(decisions):
            raise HTTPException(status_code=404, detail='cycle out of range')
        cutoff = int(cycle)

    selected_counts: Dict[str, int] = {}
    sequence: List[str] = []
    for decision in decisions[:cutoff]:
        selected = str(decision.selected_priority or '')
        if not selected:
            continue
        sequence.append(selected)
        selected_counts[selected] = selected_counts.get(selected, 0) + 1

    return {
        'status': 'ok',
        'cycle': cutoff,
        'total_cycles': len(decisions),
        'latest_selected_priority': sequence[-1] if sequence else None,
        'selected_counts': selected_counts,
        'recent_sequence': sequence[-10:],
    }


@router.post("/executive/agenda/simulate")
async def executive_agenda_simulate(request: Request):
    controller = _get_executive_controller(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='payload must be an object')

    signals = payload.get('signals')
    if signals is None:
        signals = []
    if not isinstance(signals, list):
        raise HTTPException(status_code=400, detail='signals must be an array')

    policy = payload.get('policy')
    if policy is not None and not isinstance(policy, dict):
        raise HTTPException(status_code=400, detail='policy must be an object when provided')

    defer_threshold = payload.get('defer_threshold', 45)
    try:
        normalized_threshold = int(defer_threshold)
    except Exception as exc:
        raise HTTPException(status_code=400, detail='defer_threshold must be an integer') from exc

    simulation = controller.simulate_agenda_loop(
        signals=[dict(item) for item in signals if isinstance(item, dict)],
        defer_threshold=normalized_threshold,
        policy_override=policy,
    )
    return {'status': 'ok', 'simulation': simulation}


@router.get("/executive/intents")
async def executive_intents(request: Request, status: str | None = None, limit: int = 100):
    controller = _get_executive_controller(request)
    normalized_limit = max(1, min(int(limit), 500))
    intents = controller.store.list_intents(status=status)
    intents_sorted = sorted(intents, key=lambda item: item.created_at, reverse=True)
    selected = intents_sorted[:normalized_limit]
    return {
        'status': 'ok',
        'count': len(selected),
        'items': [item.to_dict() for item in selected],
    }


@router.get("/executive/intents/{intent_id}")
async def executive_intent_by_id(intent_id: str, request: Request):
    controller = _get_executive_controller(request)
    intent = controller.store.get_intent(intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail='intent not found')
    return {'status': 'ok', 'intent': intent.to_dict()}


@router.post("/executive/intents/{intent_id}/status")
async def executive_intent_status_update(intent_id: str, request: Request):
    controller = _get_executive_controller(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='payload must be an object')
    next_status = payload.get('status')
    if not isinstance(next_status, str) or not next_status.strip():
        raise HTTPException(status_code=400, detail='status is required')
    completion_state = payload.get('completion_state')
    try:
        updated = controller.update_intent_status(
            intent_id,
            status=next_status.strip().lower(),
            completion_state=(str(completion_state) if completion_state is not None else None),
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith('unknown intent:'):
            raise HTTPException(status_code=404, detail='intent not found') from exc
        raise HTTPException(status_code=400, detail=message) from exc
    return {'status': 'ok', 'intent': updated.to_dict()}


@router.get('/executive/slo')
async def executive_operational_slo(request: Request):
    controller = _get_executive_controller(request)
    return controller.get_operational_slo_snapshot()


@router.get('/executive/intent-outcomes')
async def executive_intent_outcomes(request: Request, limit: int = 100):
    controller = _get_executive_controller(request)
    normalized_limit = max(1, min(int(limit), 500))
    items = controller.list_intent_outcomes(limit=normalized_limit)
    return {
        'status': 'ok',
        'count': len(items),
        'items': items,
    }


@router.get('/scheduler/status')
async def scheduler_status(request: Request):
    scheduler = _get_bounded_scheduler(request)
    return {
        'status': 'ok',
        'scheduler': scheduler.status(),
    }


@router.get('/scheduler/history')
async def scheduler_history(request: Request, limit: int = 50):
    scheduler = _get_bounded_scheduler(request)
    normalized_limit = max(1, min(int(limit), 500))
    items = scheduler.history(limit=normalized_limit)
    return {
        'status': 'ok',
        'count': len(items),
        'items': items,
    }


@router.get('/scheduler/halt-reasons')
async def scheduler_halt_reasons(request: Request):
    scheduler = _get_bounded_scheduler(request)
    return {
        'status': 'ok',
        'halt_reasons': scheduler.halt_reasons(),
    }


@router.post('/scheduler/run-once')
async def scheduler_run_once(request: Request):
    scheduler = _get_bounded_scheduler(request)
    if not scheduler.state.enabled:
        scheduler.start()
    result = scheduler.run_once()
    return {
        'status': 'ok',
        'result': result,
    }


@router.post('/scheduler/run-cycles')
async def scheduler_run_cycles(request: Request):
    scheduler = _get_bounded_scheduler(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='payload must be an object')
    cycles = payload.get('cycles', 1)
    try:
        normalized_cycles = max(1, min(int(cycles), 500))
    except Exception as exc:
        raise HTTPException(status_code=400, detail='cycles must be an integer') from exc
    if not scheduler.state.enabled:
        scheduler.start()
    result = scheduler.run_cycles(normalized_cycles)
    return {
        'status': 'ok',
        'result': result,
    }


@router.post('/scheduler/run-until-halt')
async def scheduler_run_until_halt(request: Request):
    scheduler = _get_bounded_scheduler(request)
    payload = await request.json()
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='payload must be an object')
    max_cycles = payload.get('max_cycles', 100)
    try:
        normalized_max_cycles = max(1, min(int(max_cycles), 1000))
    except Exception as exc:
        raise HTTPException(status_code=400, detail='max_cycles must be an integer') from exc
    if not scheduler.state.enabled:
        scheduler.start()
    result = scheduler.run_until_halt(normalized_max_cycles)
    return {
        'status': 'ok',
        'result': result,
    }


@router.get('/scheduler/sessions')
async def scheduler_sessions(request: Request, limit: int = 50):
    scheduler = _get_bounded_scheduler(request)
    normalized_limit = max(1, min(int(limit), 500))
    sessions = scheduler.list_sessions(limit=normalized_limit)
    return {
        'status': 'ok',
        'count': len(sessions),
        'items': sessions,
    }


@router.get('/scheduler/sessions/{session_id}')
async def scheduler_session_by_id(session_id: str, request: Request):
    scheduler = _get_bounded_scheduler(request)
    session = scheduler.get_session(session_id)
    if not isinstance(session, dict):
        raise HTTPException(status_code=404, detail='scheduler session not found')
    return {
        'status': 'ok',
        'session': session,
    }


@router.get('/scheduler/sessions/{session_id}/replay')
async def scheduler_session_replay(session_id: str, request: Request):
    scheduler = _get_bounded_scheduler(request)
    replay = scheduler.replay_session(session_id)
    if not bool(replay.get('found')):
        raise HTTPException(status_code=404, detail='scheduler session not found')
    return {
        'status': 'ok',
        'replay': replay,
    }


@router.post('/a2a/messages')
async def a2a_send_message(request: Request):
    router_instance = _get_a2a_router(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='payload must be an object')

    required_fields = ['sender', 'receiver', 'message_type', 'session_id']
    for field_name in required_fields:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(status_code=400, detail=f'{field_name} is required')

    request_payload = payload.get('payload')
    if request_payload is None:
        request_payload = {}
    if not isinstance(request_payload, dict):
        raise HTTPException(status_code=400, detail='payload field must be an object')

    try:
        message = router_instance.send_message(
            sender=str(payload['sender']).strip().lower(),
            receiver=str(payload['receiver']).strip().lower(),
            message_type=str(payload['message_type']).strip(),
            payload=dict(request_payload),
            session_id=str(payload['session_id']).strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return {
        'status': 'ok',
        'message': message,
    }


@router.post('/a2a/messages/{message_id}/response')
async def a2a_respond_message(message_id: str, request: Request):
    router_instance = _get_a2a_router(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='payload must be an object')

    response_payload = payload.get('response')
    if response_payload is None:
        response_payload = {}
    if not isinstance(response_payload, dict):
        raise HTTPException(status_code=400, detail='response must be an object')

    try:
        message = router_instance.respond_message(message_id, response_payload)
    except ValueError as exc:
        if str(exc) == 'a2a_message_not_found':
            raise HTTPException(status_code=404, detail='a2a message not found') from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        'status': 'ok',
        'message': message,
    }


@router.get('/a2a/messages/{message_id}')
async def a2a_get_message(message_id: str, request: Request):
    router_instance = _get_a2a_router(request)
    message = router_instance.get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail='a2a message not found')
    return {
        'status': 'ok',
        'message': message,
    }


@router.get('/a2a/sessions/{session_id}/messages')
async def a2a_session_messages(session_id: str, request: Request, limit: int = 100):
    router_instance = _get_a2a_router(request)
    normalized_limit = max(1, min(int(limit), 500))
    items = router_instance.list_session_messages(session_id=session_id, limit=normalized_limit)
    return {
        'status': 'ok',
        'count': len(items),
        'items': items,
    }


@router.get('/a2a/inbox/{receiver}')
async def a2a_inbox(receiver: str, request: Request, limit: int = 100, session_id: str | None = None):
    router_instance = _get_a2a_router(request)
    normalized_limit = max(1, min(int(limit), 500))
    items = router_instance.inbox(receiver=receiver, session_id=session_id, limit=normalized_limit)
    return {
        'status': 'ok',
        'count': len(items),
        'items': items,
    }


@router.post('/a2a/workflows/research-prototype')
async def a2a_workflow_research_prototype(request: Request):
    router_instance = _get_a2a_router(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail='payload must be an object')

    session_id = payload.get('session_id')
    topic = payload.get('topic')
    if not isinstance(session_id, str) or not session_id.strip():
        raise HTTPException(status_code=400, detail='session_id is required')
    if not isinstance(topic, str) or not topic.strip():
        raise HTTPException(status_code=400, detail='topic is required')

    try:
        workflow = router_instance.run_research_prototype_workflow(
            session_id=session_id.strip(),
            topic=topic.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return {
        'status': 'ok',
        'workflow': workflow,
    }



# ---- Legacy Compatibility Endpoints ----
_AGENT_ALIASES = {"cryptonia_historical_agent": "coinmarketcap_agent"}

@router.get("/agents/aliases")
async def agent_aliases():
    return dict(_AGENT_ALIASES)

@router.get("/agents/capabilities")
async def agents_capabilities():
    return {
        "capabilities": ["crypto_data", "crypto_strategy"],
        "allowedActiveCapabilities": ["crypto_data", "crypto_strategy"],
    }

@router.post("/frontend/issues")
async def frontend_issues(request: Request):
    data = await request.json()
    files = data.get("files") if isinstance(data.get("files"), list) else []
    task = {
        "type": "frontend_issue",
        "preferredNode": "thinkpad",
        "payload": {
            "issue": data.get("issue"),
            "context": data.get("context", ""),
            "files": files,
        },
    }
    return {"status": "queued", "task": task}

@router.post("/cryptonia/capital/orchestrate")
async def cryptonia_capital_orchestrate(request: Request):
    from andie.trading.orchestrator import run_capital_orchestration
    data = await request.json()
    return run_capital_orchestration(data if isinstance(data, dict) else {})

@router.post("/cryptonia/overseer/run")
async def cryptonia_overseer_run(request: Request):
    from andie.core.agents.coinmarketcap_agent import run_agent as _run_coinmarketcap
    from andie.core.agents.cryptonia_strategy_agent import run_agent as _run_strategy

    data = await request.json()
    data_capability = str(data.get("data_capability") or "crypto_data")
    strategy_capability = str(data.get("strategy_capability") or "crypto_strategy")
    active_caps = sorted([data_capability, strategy_capability])
    if active_caps != ["crypto_data", "crypto_strategy"]:
        raise HTTPException(status_code=400, detail="exactly two active capabilities are required: crypto_data and crypto_strategy")

    data_agent_requested = str(data.get("data_agent") or "cryptonia_historical_agent")
    data_agent_resolved = _AGENT_ALIASES.get(data_agent_requested, data_agent_requested)
    strategy_agent_requested = str(data.get("strategy_agent") or "cryptonia_strategy_agent")

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    constraints = data.get("constraints") if isinstance(data.get("constraints"), dict) else {}

    data_result = _run_coinmarketcap({
        "prompt": str(data.get("task") or ""),
        "metadata": metadata,
    })
    series = data_result.get("series") if isinstance(data_result, dict) and isinstance(data_result.get("series"), list) else []
    strategy_result = _run_strategy({
        "metadata": {
            "constraints": constraints,
            "market_data": {"series": series},
        }
    })

    confidence = float(strategy_result.get("confidence") or 0.0) if isinstance(strategy_result, dict) else 0.0
    risk_score = float(strategy_result.get("risk_score") or 1.0) if isinstance(strategy_result, dict) else 1.0
    composite_score = max(0.0, min(1.0, (confidence * 0.7) + ((1.0 - risk_score) * 0.3)))
    decision = "approve" if composite_score >= 0.55 else "hold"

    evaluation = {
        "decision": decision,
        "profile": str(data.get("profile") or "balanced"),
        "composite_score": composite_score,
        "weights": {"confidence": 0.7, "risk_inverse": 0.3},
        "reason_trace": ["compat_overseer_evaluation"],
    }

    andie_decision = {
        "profile": evaluation["profile"],
        "composite_score": composite_score,
        "risk_adjusted": max(0.0, min(1.0, confidence * (1.0 - risk_score))),
        "weights": evaluation["weights"],
        "signals": {"confidence": confidence, "risk_score": risk_score},
        "reason_trace": evaluation["reason_trace"],
        "execution": "buy" if decision == "approve" else "hold",
    }

    return {
        "status": "ok",
        "mode": "dual_agent_overseer",
        "activeCapabilities": ["crypto_data", "crypto_strategy"],
        "agentResolution": {
            "data": {"requested": data_agent_requested, "resolved": data_agent_resolved},
            "strategy": {"requested": strategy_agent_requested, "resolved": "cryptonia_strategy_agent"},
        },
        "data": {"raw": data_result, "normalized": {"type": "market_data", "series": series}},
        "strategy": {"raw": strategy_result, "normalized": {"type": "strategy", "action": (strategy_result or {}).get("action") if isinstance(strategy_result, dict) else None}},
        "evaluation": evaluation,
        "andieDecision": andie_decision,
    }

@router.post("/events/publish")
async def events_publish(request: Request):
    from andie_backend.interfaces.api.event_bus import emit_event
    from andie_backend.interfaces.api.trading_approvals import process_trading_approval_event
    event = await request.json()
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="event payload must be an object")
    processed = process_trading_approval_event(event)
    await emit_event(processed)
    return {"status": "published", "event": processed}

@router.get("/trading/approvals")
async def trading_approvals_list(includeResolved: bool = False):
    from andie_backend.interfaces.api.trading_approvals import list_trade_approvals
    return {"items": list_trade_approvals(include_resolved=includeResolved)}

async def execute_approved_trade(approval: Dict[str, Any], metadata: Dict[str, Any] | None = None):
    return {"status": "noop", "execution": {"status": "simulated"}}

@router.post("/trading/approvals/{approval_id}/reject")
async def trading_approval_reject(approval_id: str, request: Request):
    from andie_backend.interfaces.api.trading_approvals import get_trade_approval, resolve_trade_approval
    data = await request.json()
    approval = get_trade_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    updated = resolve_trade_approval(approval_id, "rejected", actor=data.get("actor"), reason=data.get("reason"))
    return {"status": "rejected", "approval": updated}

@router.post("/trading/approvals/{approval_id}/approve")
async def trading_approval_approve(approval_id: str, request: Request):
    from andie_backend.interfaces.api.trading_approvals import get_trade_approval, resolve_trade_approval
    data = await request.json()
    approval = get_trade_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    execution = await execute_approved_trade(approval, metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None)
    updated = resolve_trade_approval(approval_id, "approved", actor=data.get("actor"), reason=data.get("reason"))
    return {"status": "approved", "approval": updated, "execution": execution}

# Compatibility globals for legacy tests
try:
    from autonomy.memory_store import MemoryStore
    skill_learning_memory = MemoryStore(os.environ.get("ANDIE_SKILL_MEMORY_PATH", "/tmp/skill_memory.json"))
except Exception:
    skill_learning_memory = None

# Compatibility export for tests importing interfaces.api.main:app
app = FastAPI()
app.include_router(router)
