import asyncio
import os
from typing import Any, Dict, Optional

class Agent:
    def __init__(self, name: str, orchestrator=None):
        self.name = name
        self.orchestrator = orchestrator
        self.running = False
        self._knowledge_enabled = os.environ.get("ANDIE_AUTONOMY_DISABLE_KNOWLEDGE", "").lower() != "true"

    async def run(self):
        """Override this in subclasses with agent's main loop."""
        raise NotImplementedError

    async def report(self, data: Dict[str, Any]):
        if self.orchestrator:
            await self.orchestrator.receive_report(self.name, data)

    def set_orchestrator(self, orchestrator):
        self.orchestrator = orchestrator
    
    def enrich_context_with_knowledge(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich agent context with knowledge guidance.
        Override or extend this method in subclasses for custom enrichment.
        """
        if not self._knowledge_enabled:
            return context
        
        try:
            from autonomy.knowledge_integration import enrich_autonomy_context
            return enrich_autonomy_context(context, knowledge_enabled=True)
        except (ImportError, Exception):
            # Graceful fallback if knowledge module unavailable
            return context
