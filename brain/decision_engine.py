import re
from typing import Any, Dict, List, Optional


# Keyword → (intent, agent) routing table used by the stub LLM layer.
# Replace the _stub_llm() method with a real LLM call when ready.
_INTENT_TABLE = [
    (["btc", "price", "market", "crypto", "coin"],          "crypto_data",     "coinmarketcap_agent"),
    (["strategy", "trade", "entry", "signal", "plan"],      "crypto_strategy", "strategy_agent"),
    (["health", "status", "system", "ping"],                "system_health",   "health_agent"),
    (["recover", "fix", "error", "heal", "repair"],         "self_recovery",   "recovery_agent"),
    (["autonomy", "tick", "loop"],                          "autonomy_tick",   "health_agent"),
    (["memory", "remember", "recall", "history", "earlier", "before", "last", "previous"],
                                                            "memory_lookup",   "memory_agent"),
]
_DEFAULT_INTENT = "default"
_DEFAULT_AGENT  = "fallback_agent"

# Minimum number of context hits to trigger agent reinforcement
_CONTEXT_REINFORCE_THRESHOLD = 2


class DecisionEngine:
    """
    Context-aware decision layer.

    Public contract:
        decision = await engine.decide(message, context)

    `context` is a list of recent MemoryService entries shaped as:
        {"agent": str, "input": str, "output": dict, "timestamp": float}

    The engine uses BOTH the raw message keywords AND the context to decide
    intent / agent — so the same question asked repeatedly will converge on
    the same agent, and contradictory signals will trigger a switch.

    LLM upgrade path: replace ``_stub_llm()`` with a real LLM call.
    The ``build_prompt()`` helper is already wired to produce the prompt.
    """

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    async def decide(
        self,
        message: str,
        context: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Returns:
            {
                "intent":       str,
                "agent":        str,
                "reason":       str,
                "confidence":   float,   # 0.0–1.0
                "context_used": int,     # number of context entries considered
            }
        """
        context = context or []
        text = message.lower()

        # Greeting intent detection
        if any(word in text for word in ["hello", "hi", "hey", "yo"]):
            return {
                "intent": "greeting",
                "agent": "fallback_agent",
                "reason": "greeting detected",
                "confidence": 0.9,
                "context_used": len(context)
            }

        # If no context, return unknown intent
        if not context:
            return {
                "intent": "unknown",
                "agent": "fallback_agent",
                "reason": "no context or keyword match",
                "confidence": 0.3,
                "context_used": 0
            }

        intent, agent, reason, confidence = await self._stub_llm(message, context)
        return {
            "intent":       intent,
            "agent":        agent,
            "reason":       reason,
            "confidence":   confidence,
            "context_used": len(context),
        }

    # ------------------------------------------------------------------ #
    #  LLM upgrade hook                                                    #
    # ------------------------------------------------------------------ #

    def build_prompt(
        self,
        message: str,
        context: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        Builds a structured prompt string suitable for a real LLM call.
        Swap ``_stub_llm`` for::

            async def _llm(self, message, context):
                prompt = self.build_prompt(message, context)
                raw = await your_llm_client(prompt)
                return parse(raw)   # → (intent, agent, reason, confidence)
        """
        context = context or []
        history_lines = []
        for i, entry in enumerate(context[-5:], 1):          # last 5 entries max
            history_lines.append(
                f"  [{i}] agent={entry.get('agent','?')} "
                f"input={entry.get('input','')!r} "
                f"output_keys={list(entry.get('output', {}).keys()) if isinstance(entry.get('output'), dict) else '...'}"
            )
        history_block = "\n".join(history_lines) or "  (none)"

        return (
            f"You are a routing engine. Given the user message and recent history, "
            f"return JSON with keys: intent, agent, reason, confidence (0–1).\n\n"
            f"AVAILABLE AGENTS: {[row[2] for row in _INTENT_TABLE] + [_DEFAULT_AGENT]}\n\n"
            f"RECENT HISTORY:\n{history_block}\n\n"
            f"USER MESSAGE: {message!r}\n\n"
            f"RESPOND WITH JSON ONLY."
        )

    # ------------------------------------------------------------------ #
    #  Internal — keyword stub (replace with LLM when ready)              #
    # ------------------------------------------------------------------ #

    async def _stub_llm(
        self,
        message: str,
        context: List[Dict[str, Any]],
    ):
        """
        Returns (intent, agent, reason, confidence).

        Context rules applied (in order):
        1. If context entries repeatedly used the same agent for a similar
           topic → reinforce (confidence boost, keep that agent).
        2. If the raw message strongly matches a different intent → switch.
        3. If context mentions recall/history keywords → route to memory_agent.
        """
        lower = message.lower()

        # --- Step A: keyword match on raw message ---
        msg_intent = _DEFAULT_INTENT
        msg_agent  = _DEFAULT_AGENT
        msg_reason = "no keyword matched; using default"
        for keywords, intent, agent in _INTENT_TABLE:
            if any(re.search(r"\b" + kw + r"\b", lower) for kw in keywords):
                msg_intent = intent
                msg_agent  = agent
                msg_reason = f"keyword match: {intent}"
                break

        if not context:
            return msg_intent, msg_agent, msg_reason, 0.6

        # --- Step B: tally agent usage in context ---
        agent_counts: Dict[str, int] = {}
        for entry in context:
            a = entry.get("agent", "")
            if a:
                agent_counts[a] = agent_counts.get(a, 0) + 1

        dominant_agent  = max(agent_counts, key=agent_counts.get) if agent_counts else None
        dominant_count  = agent_counts.get(dominant_agent, 0) if dominant_agent else 0

        # --- Step C: apply context logic ---
        if dominant_count >= _CONTEXT_REINFORCE_THRESHOLD and dominant_agent == msg_agent:
            # Context and message agree — high confidence
            return (
                msg_intent, msg_agent,
                f"{msg_reason} (context reinforced ×{dominant_count})",
                min(0.95, 0.6 + 0.1 * dominant_count),
            )

        if dominant_count >= _CONTEXT_REINFORCE_THRESHOLD and msg_agent == _DEFAULT_AGENT:
            # Message is ambiguous but context has a clear preference → follow context
            ctx_intent = next(
                (row[1] for row in _INTENT_TABLE if row[2] == dominant_agent),
                _DEFAULT_INTENT,
            )
            return (
                ctx_intent, dominant_agent,
                f"context dominant agent ({dominant_agent} ×{dominant_count}); message was ambiguous",
                min(0.85, 0.5 + 0.1 * dominant_count),
            )

        # Message and context conflict — trust message (higher signal)
        return (
            msg_intent, msg_agent,
            f"{msg_reason} (overrides context dominant={dominant_agent})",
            0.55,
        )

