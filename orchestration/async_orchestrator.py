
import re


import asyncio
import logging
from typing import Dict, Any, Optional

# You already have these in your codebase
from andie_backend.autonomy.autonomy_profiles import DEFAULT_PROFILE
from andie_backend.autonomy.trigger_engine import TriggerEngine
from andie_backend.core.async_core.event_system import EventSystem
from andie_backend.brain.decision_engine import DecisionEngine
from andie_backend.agents.loader import AgentLoader
from andie_backend.autonomy.governance import evaluate_go_no_go
from andie_backend.autonomy.control_plane_metrics import control_plane_metrics
from andie_backend.autonomy.runtime_config import get_runtime_config

# Lazy import of ws_hub to avoid circular imports at module load time
def _get_ws_hub():
    try:
        from andie_backend.interfaces.api.ws_hub import ws_hub
        return ws_hub
    except Exception:
        return None

logger = logging.getLogger(__name__)


class IntentRouter:
    def route(self, message: str) -> str:
        msg = message.lower()

        if any(k in msg for k in ["btc", "price", "market", "crypto"]):
            return "crypto_data"

        if any(k in msg for k in ["strategy", "trade", "entry"]):
            return "crypto_strategy"

        if any(k in msg for k in ["health", "status", "system"]):
            return "system_health"

        if any(k in msg for k in ["recover", "fix", "error"]):
            return "self_recovery"

        return "default"


class AgentRegistry:
    def __init__(self):
        self.registry = {
            "crypto_data": "coinmarketcap_agent",
            "crypto_strategy": "cryptonia_strategy_agent",
            "system_health": "health_agent",
            "self_recovery": "recovery_agent",
            "default": "fallback_agent",
        }

    def get_agent(self, intent: str) -> str:
        return self.registry.get(intent, "fallback_agent")



class AsyncOrchestrator:
    def __init__(self):
        self.router = IntentRouter()
        self.registry = AgentRegistry()
        self.event_system = EventSystem()
        self.trigger_engine = TriggerEngine()
        self.profile = DEFAULT_PROFILE
        self.decision_engine = DecisionEngine()
        self.agent_loader = AgentLoader()
        self.memory = None  # injected from FastAPI
        self.event_system.register("agent.request",  self._handle_agent_request)
        self.event_system.register("agent.response", self._handle_agent_response)

    # --- STEP 1: Extract user identity from message ---
    def _extract_user_identity(self, message: str):
        match = re.search(r"my name is (\w+)", message.lower())
        if match:
            return match.group(1).capitalize()
        return None

    # --- STEP 3: Retrieve user name from context ---
    def _get_user_name(self, context):
        for entry in context:
            if entry.get("type") == "user_profile" and entry.get("key") == "name":
                return entry.get("value")
        return None

    async def run(self, *args, **kwargs):
        return {"status": "ok"}

    # 🔥 MAIN ENTRY POINT
    async def handle(self, message: str) -> Dict[str, Any]:
        # Runtime config guard — honour forced_mode="incident" as emergency stop
        config = get_runtime_config()
        if config.get("forced_mode") == "incident":
            return {"status": "paused", "reason": "forced_mode=incident (emergency stop active)"}

        try:
            import time as _time

            # 1️⃣ Search memory for relevant past interactions (context)
            context = []
            if self.memory:
                context = await self.memory.search(message)

            # --- HARD FILTER: Remove system noise defensively ---
            context = [
                e for e in context
                if not e.get("input", "").startswith("autonomy:")
                and e.get("agent") != "health_agent"
            ]

            # --- STEP 2: Structured identity extraction and storage ---
            name = self._extract_user_identity(message)
            if name and self.memory:
                existing_name = self._get_user_name(await self.memory.search("name"))
                if existing_name != name:
                    await self.memory.add({
                        "type": "user_profile",
                        "key": "name",
                        "value": name,
                        "timestamp": _time.time()
                    })

            # 🧠 Identity shortcut (bypass agents)
            if "name" in message.lower():
                for entry in context:
                    text = entry.get("input", "").lower()
                    if "my name is" in text:
                        name = text.split("my name is")[-1].strip().split()[0].capitalize()
                        return {
                            "status": "ok",
                            "response": f"Your name is {name}",
                            "agent": "identity_system"
                        }
                return {
                    "status": "ok",
                    "response": "I don't know your name yet.",
                    "agent": "identity_system"
                }

            # 2️⃣ Context-aware decision
            decision = await self.decision_engine.decide(message, context)
            intent     = decision["intent"]
            agent      = decision["agent"]
            confidence = decision.get("confidence", 0.0)

            # 3️⃣ Emit agent.request — event handler also executes the agent
            payload = {"agent": agent, "message": message, "context": context}
            await self.event_system.emit("agent.request", payload)

            # 4️⃣ Run agent directly with context so we return an inline result
            result = await self.execute_agent(agent, message, context=context)

            # 5️⃣ Write structured memory entry (new canonical schema)
            if self.memory:
                try:
                    self.memory.add({
                        "agent":     agent,
                        "input":     message,
                        "output":    result if isinstance(result, dict) else {"response": str(result)},
                        "timestamp": _time.time(),
                        # keep legacy fields so old memory readers don't break
                        "role":      "assistant",
                        "content":   str(result),
                    })
                except Exception as _mem_write_exc:
                    logger.warning("Memory write failed: %s", _mem_write_exc)

            # 6️⃣ Emit orchestrator.response
            await self.event_system.emit("orchestrator.response", {
                "intent":       intent,
                "agent":        agent,
                "result":       result,
                "reason":       decision.get("reason"),
                "confidence":   confidence,
                "context_used": decision.get("context_used", 0),
            })

            return {
                "intent":       intent,
                "agent":        agent,
                "result":       result,
                "reason":       decision.get("reason"),
                "confidence":   confidence,
                "context_used": decision.get("context_used", 0),
            }

        except Exception as e:
            return {
                "error": str(e),
                "status": "failed"
            }

    # 📡 EVENT HANDLER — invoked by event_system on "agent.request"
    async def _handle_agent_request(self, payload: Dict[str, Any]):
        agent_name = payload.get("agent", "fallback_agent")
        message    = payload.get("message", "")

        # 🛡 Governance gate — evaluate before execution
        metrics_snapshot = control_plane_metrics.to_dict().get("rates", {})
        go = evaluate_go_no_go({
            "sample_size":               control_plane_metrics.to_dict()["counters"].get("outcome_events_total", 0),
            "real_sample_size":          control_plane_metrics.to_dict()["counters"].get("real_outcome_events_total", 0),
            "replacement_success_rate":  metrics_snapshot.get("replacement_success_rate") or 0.0,
            "drift_rate":                metrics_snapshot.get("score_drift_rate") or 0.0,
            "learning_density":          metrics_snapshot.get("learning_signal_density") or 0.0,
        })

        if go["decision"] == "NO_GO" and go["confidence_tier"] == "production":
            # Only block in production tier — allow synthetic/mixed to proceed
            logger.warning("Governance NO_GO [%s]: %s", agent_name, go["reasons"])
            control_plane_metrics.increment("governance_blocks")
            await self.event_system.emit("agent.response", {
                "agent":   agent_name,
                "message": message,
                "blocked": True,
                "reason":  go["reasons"],
                "result":  {"status": "blocked_by_governance"},
            })
            return

        try:
            run     = self.agent_loader.safe_load(agent_name)
            context = payload.get("context", [])

            # 📡 Broadcast "thinking" to all WS clients
            hub = _get_ws_hub()
            if hub:
                asyncio.create_task(hub.broadcast({
                    "type": "agent.thinking",
                    "agent": agent_name,
                    "message": message,
                }))

            # Pass context into agent (agents must accept context=None kwarg)
            result = await _call_agent(run, message, context)

            # Emit response event for downstream listeners
            await self.event_system.emit("agent.response", {
                "agent":   agent_name,
                "message": message,
                "result":  result,
            })

        except Exception as exc:
            logger.error(
                "Event agent handler error (%s): %s", agent_name, exc, exc_info=True
            )
            await self.event_system.emit("agent.response", {
                "agent":   agent_name,
                "message": message,
                "result":  {"error": str(exc)},
            })

    # 📬 EVENT HANDLER — invoked by event_system on "agent.response"
    async def _handle_agent_response(self, payload: Dict[str, Any]):
        """Central sink for all agent results — feeds metrics, broadcasts to WebSocket clients."""
        agent_name = payload.get("agent", "unknown")
        result     = payload.get("result", {})
        blocked    = payload.get("blocked", False)

        # Feed outcome metrics
        control_plane_metrics.increment("outcome_events_total")
        if not blocked and "error" not in (result or {}):
            control_plane_metrics.increment("real_outcome_events_total")

        # 📡 Broadcast completed result to all WebSocket clients
        hub = _get_ws_hub()
        if hub:
            asyncio.create_task(hub.broadcast({
                "type":    "agent.response",
                "agent":   agent_name,
                "blocked": blocked,
                "result":  result,
                "message": payload.get("message"),
            }))

        logger.debug("agent.response [%s] blocked=%s: %s", agent_name, blocked, result)

    # 🔌 AGENT EXECUTION LAYER
    async def execute_agent(
        self,
        agent_name: str,
        message: str,
        context: list = None,
    ) -> Dict[str, Any]:
        """
        Try plugin loader first; fall back to structured stub.
        Passes context to the agent when supported.
        """
        context = context or []
        try:
            run = self.agent_loader.load(agent_name)
            return await _call_agent(run, message, context)
        except Exception:
            pass

        # Legacy stub fallback
        await asyncio.sleep(0)
        return {
            "agent": agent_name,
            "response": f"[{agent_name}] handled: {message}",
            "context_used": len(context),
        }


# ---------------------------------------------------------------------------
# Module-level helper — calls an agent run() with or without context kwarg
# ---------------------------------------------------------------------------
import inspect as _inspect


async def _call_agent(run_fn, message: str, context: list):
    """
    Calls ``run_fn(message)`` or ``run_fn(message, context=context)``
    depending on the agent's signature.  This keeps old agents working
    unchanged while new ones can consume context.
    """
    sig = _inspect.signature(run_fn)
    if "context" in sig.parameters:
        return await run_fn(message, context=context)
    return await run_fn(message)
