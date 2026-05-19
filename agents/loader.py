import importlib
import logging
from typing import Callable

logger = logging.getLogger(__name__)


class AgentLoader:
    """
    Dynamically loads an agent module from andie_backend.agents.<agent_name>
    and returns its async `run` callable.

    Contract: every agent module MUST expose a top-level async `run(message: str)`.
    Failures are explicit (ValueError) so the caller decides how to handle them.
    """

    _cache: dict = {}

    def load(self, agent_name: str) -> Callable:
        if agent_name in self._cache:
            return self._cache[agent_name]

        module_path = f"andie_backend.agents.{agent_name}"
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            raise ValueError(f"Agent module {module_path!r} not found") from exc

        if not hasattr(module, "run"):
            raise ValueError(f"Agent module {module_path!r} missing required `run()` callable")

        self._cache[agent_name] = module.run
        return module.run

    def safe_load(self, agent_name: str) -> Callable:
        """Like load(), but falls back to a stub instead of raising."""
        try:
            return self.load(agent_name)
        except ValueError as exc:
            logger.warning("%s — using fallback stub", exc)
            return _fallback_run


async def _fallback_run(message: str, context=None):
    return {"agent": "fallback", "response": f"[fallback] no agent found for: {message}", "context_used": len(context) if context else 0}
