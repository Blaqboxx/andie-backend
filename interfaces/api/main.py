# --- ANDIE API: MCP + Sentinel integration ---
# --- ANDIE API: MCP + Sentinel integration ---
import httpx
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException
from andie_backend.interfaces.api.event_bus import subscribe, unsubscribe, recent_events
import asyncio
import json as py_json
import time

from andie_backend.inference.router import chat as _ollama_chat
from andie_backend.andie.brain.system_prompt import build_system_prompt
from andie_backend.andie.action.action_router import route as _action_route
from andie_backend.andie.memory.observer import observe as _observe

router = APIRouter()
# --- ANDIE API: MCP + Sentinel integration ---
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue = await subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(py_json.dumps(event))
    except WebSocketDisconnect:
        await unsubscribe(queue)

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
    ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

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
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{MCP_URL}/docs")
            status["mcp"] = "online" if r.status_code == 200 else "unknown"
    except:
        status["mcp"] = "offline"

    # Sentinel check (HTTP)
    try:
        sentinel_url = os.environ.get("SENTINEL_URL", "http://127.0.0.1:7002")
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{sentinel_url}/health")
            status["sentinel"] = "online" if r.status_code == 200 else "unknown"
    except:
        status["sentinel"] = "offline"

    # Ollama runtime telemetry (low-cost probe against /api/tags).
    ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    status["ollama"] = await _ollama_telemetry(ollama_host)

    # Memory/embedding readiness from app lifespan state.
    try:
        memory_service = getattr(request.app.state, "memory_service", None)
        if memory_service is not None and hasattr(memory_service, "health"):
            status["memory"] = memory_service.health()
        else:
            status["memory"] = {
                "embeddings_enabled": False,
                "degraded": True,
                "degraded_reason": "Memory service not initialized",
                "vector_entries": 0,
            }
    except Exception as e:
        status["memory"] = {
            "embeddings_enabled": False,
            "degraded": True,
            "degraded_reason": str(e),
            "vector_entries": 0,
        }

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


@router.get("/guardian")
async def guardian_status():
    async with httpx.AsyncClient() as client:
        r = await client.get("http://127.0.0.1:7010/health")
        return r.json()


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


# --- ANDIE API: Chat Endpoint ---
@router.post("/chat")
async def chat(request: Request):
    try:
        data = await request.json()
        user_message = data.get("message", "")


        _sys = build_system_prompt()
        result = await _action_route(user_message, _ollama_chat, _sys)
        if "meta" not in result:
            result["meta"] = {"source": "ollama", "intent": result.get("intent", "CHAT_RESPONSE")}
        result.setdefault("confidence", 0.92)


        # Store structured memory (user + assistant) — optional
        try:
            memory: MemoryService = request.app.state.memory_service
            memory.add({
                "role": "user",
                "content": user_message
            })
            memory.add({
                "role": "assistant",
                "content": result.get("response", ""),
                "confidence": result.get("confidence"),
                "meta": result.get("meta")
            })
        except AttributeError:
            pass

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

@router.post("/orchestrator/run")
async def orchestrator_run(request: Request):
    try:
        data = await request.json()
        task = data.get("task", data.get("message", ""))
        context = data.get("context", "")
        system_prompt = "You are ANDIE (Autonomous Neural Distributed Intelligence Engine), an AI system running on a distributed home cluster. You are concise, direct, and technically precise. You assist the operator Jamai with system management, code, and autonomous tasks. Never pretend to be a generic AI assistant."
        if context:
            system_prompt = system_prompt + " " + context
        result = await _ollama_chat(
            messages=[{"role": "user", "content": task}],
            system=system_prompt,
        )
        return {
            "status": "ok",
            "response": result["response"],
            "confidence": 0.92,
            "route": "assistant",
            "meta": {"source": "ollama", "model": result["model"], "node": result["node"]},
        }
    except Exception as e:
        import traceback
        print("[CHAT ERROR]", e)
        print(traceback.format_exc())
        return {"status": "error", "response": str(e), "confidence": 0.0}

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
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
        "memory_available_mb": psutil.virtual_memory().available // 1024 // 1024,
        "disk_percent": psutil.disk_usage("/").percent,
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
    task = data.get("task", f"Run {name} agent")
    system = f"You are {name}, an AI agent. Respond concisely."
    try:
        result = await _ollama_chat(messages=[{"role": "user", "content": task}], system=system)
        return {"agent": name, "response": result["response"], "status": "ok"}
    except Exception as e:
        return {"agent": name, "response": f"{name} agent unavailable: {e}", "status": "error"}

@router.get("/skills")
async def list_all_skills():
    import json
    from pathlib import Path
    skills_dir = Path("/media/jamai-jamison/78eb7352-2efe-465a-a250-c5df9c24726d/valhalla/skills")
    skills = []
    if skills_dir.exists():
        for d in skills_dir.iterdir():
            mp = d / "manifest.json"
            if mp.exists():
                try: skills.append(json.loads(mp.read_text()))
                except: pass
    return {"skills": skills, "count": len(skills)}

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


# ── Autonomy control endpoints ────────────────────────────────────────────────
_autonomy_state = {"running": False, "enabled": True, "iteration": 0}

@router.post("/autonomy/start")
async def autonomy_start():
    _autonomy_state["running"] = True
    _autonomy_state["iteration"] = _autonomy_state.get("iteration", 0) + 1
    return {"status": "started", "running": True, "iteration": _autonomy_state["iteration"]}

@router.post("/autonomy/stop")
async def autonomy_stop():
    _autonomy_state["running"] = False
    return {"status": "stopped", "running": False}

@router.post("/autonomy/disable")
async def autonomy_disable(reason: str = "operator_request"):
    _autonomy_state["enabled"] = False
    _autonomy_state["running"] = False
    return {"status": "disabled", "reason": reason, "enabled": False}

@router.post("/autonomy/enable")
async def autonomy_enable():
    _autonomy_state["enabled"] = True
    return {"status": "enabled", "enabled": True}

@router.get("/autonomy/explain")
async def autonomy_explain():
    try:
        from autonomy.autonomy_controller import AUTO_THRESHOLD, REVIEW_THRESHOLD
        from autonomy.autonomy_profiles import DEFAULT_PROFILE
        return {
            "profile": DEFAULT_PROFILE,
            "explanation": f"Operating in assisted mode. Skills with trust >= {AUTO_THRESHOLD} execute automatically. Trust between {REVIEW_THRESHOLD} and {AUTO_THRESHOLD} requires approval. Below {REVIEW_THRESHOLD} is blocked.",
            "auto_threshold": AUTO_THRESHOLD,
            "review_threshold": REVIEW_THRESHOLD,
        }
    except Exception as e:
        return {"explanation": "Autonomy engine active", "error": str(e)}

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
    try:
        from autonomy.autonomy_profiles import PROFILES, DEFAULT_PROFILE
        return {"profile": DEFAULT_PROFILE, "profiles": list(PROFILES.keys()), "state": _autonomy_state}
    except Exception as e:
        return {"profile": "balanced", "state": _autonomy_state, "error": str(e)}


# ── Advanced Autonomy endpoints ───────────────────────────────────────────────

@router.get("/autonomy/explain")
async def autonomy_explain_decision():
    try:
        from autonomy.explainer import explain_decision, LAST_DECISION_CONTEXT
        result = explain_decision(LAST_DECISION_CONTEXT)
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
        from autonomy.plan_optimizer import apply_replacements, suggest_alternatives, resolve_min_trust_threshold
        data = await request.json()
        plan = data.get("plan", [])
        profile = data.get("profile", "balanced")
        context_key = data.get("context_key")
        min_trust = resolve_min_trust_threshold(profile)
        result = apply_replacements(plan, context_key=context_key)
        suggestions = [suggest_alternatives(step, context_key=context_key) for step in plan]
        return {"status": "ok", "profile": profile, "min_trust": min_trust, "optimized": result, "suggestions": suggestions}
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
        from autonomy.plan_optimizer import suggest_alternatives, resolve_min_trust_threshold
        from autonomy.trust_engine import compute_trust
        data = await request.json()
        task = data.get("task", "")
        words = task.lower().split()
        plan = []
        for i, word in enumerate(words[:5]):
            if len(word) > 3:
                trust = compute_trust(word)
                plan.append({
                    "step": word,
                    "trust": trust,
                    "mode": "auto" if trust >= 0.75 else "approval" if trust >= 0.5 else "block",
                    "why": f"Required for: {task}",
                    "order": i + 1,
                })
        return {"plan": plan, "task": task, "steps": len(plan)}
    except Exception as e:
        return {"plan": [], "task": "", "error": str(e)}

@router.get("/skills/plan/snapshots")
async def skills_plan_snapshots():
    try:
        from pathlib import Path
        snap_dir = Path("/media/jamai-jamison/78eb7352-2efe-465a-a250-c5df9c24726d/valhalla/andie_backend/storage/plans")
        snap_dir.mkdir(parents=True, exist_ok=True)
        snapshots = [f.name for f in snap_dir.glob("*.json")]
        return {"snapshots": snapshots, "count": len(snapshots)}
    except Exception as e:
        return {"snapshots": [], "count": 0, "error": str(e)}

@router.post("/skills/plan/execute-edited")
async def skills_execute_plan(request: Request):
    try:
        from autonomy.simulation_engine import simulate_failure_scenario
        from autonomy.autonomy_controller import decide_execution_mode
        data = await request.json()
        plan = data.get("plan", [])
        results = []
        for step in plan:
            decision = decide_execution_mode(step)
            sim = simulate_failure_scenario([step], failure_rate=0.1)
            results.append({
                "step": step.get("step", step) if isinstance(step, dict) else step,
                "decision": decision,
                "simulated": sim[0] if sim else {},
            })
        return {"result": results, "status": "simulated", "steps": len(results)}
    except Exception as e:
        return {"result": [], "status": "error", "error": str(e)}

@router.post("/operator/override")
async def operator_override(request: Request):
    try:
        from autonomy.learning_engine import record_operator_feedback
        data = await request.json()
        override_type = data.get("type", "override")
        skill_name = data.get("skill_name", "")
        to_skill = data.get("to_skill", "")
        if skill_name:
            record_operator_feedback(skill_name, feedback_type=override_type, replaced_by=to_skill)
        return {"status": "ok", "type": override_type, "skill": skill_name}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@router.get("/skills/feedback")
async def skills_feedback():
    try:
        from autonomy.learning_engine import memory
        feedback = []
        for key, data in (memory.data if hasattr(memory, 'data') else {}).items():
            fb = data.get("operator_feedback", {})
            if any(fb.values()):
                feedback.append({"skill": key, "feedback": fb})
        return {"feedback": feedback, "count": len(feedback)}
    except Exception as e:
        return {"feedback": [], "count": 0, "error": str(e)}


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

