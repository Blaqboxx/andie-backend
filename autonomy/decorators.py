"""
Autonomy Decorators
Provides decorators for agents to automatically integrate knowledge and other features.
"""

import functools
import logging
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


def with_knowledge_enrichment(func: Callable) -> Callable:
    """
    Decorator to automatically enrich agent context with knowledge before execution.
    
    Usage:
        @with_knowledge_enrichment
        async def handle_event(self, context: Dict[str, Any]) -> Dict[str, Any]:
            # Your agent logic here
            pass
    """
    @functools.wraps(func)
    async def wrapper(self, context: Dict[str, Any], *args, **kwargs) -> Dict[str, Any]:
        # Enrich context if the agent has the method
        if hasattr(self, 'enrich_context_with_knowledge'):
            context = self.enrich_context_with_knowledge(context)
        
        # Call original function
        result = await func(self, context, *args, **kwargs)
        
        # Inject knowledge guidance into result if available
        if isinstance(result, dict) and isinstance(context.get('knowledge_guidance'), dict):
            if context['knowledge_guidance'].get('relevant'):
                result['knowledge_source'] = context['knowledge_guidance'].get('sources', [])
                result['knowledge_rationale'] = context['knowledge_guidance'].get('answer')
        
        return result
    
    return wrapper


def log_decision(decision_type: str) -> Callable:
    """
    Decorator to log autonomous decisions with context.
    
    Usage:
        @log_decision("trade_execution")
        async def execute_trade(self, trade: Dict[str, Any]) -> Dict[str, Any]:
            pass
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs) -> Dict[str, Any]:
            logger.info(f"[{decision_type}] Agent {getattr(self, 'name', 'unknown')} executing decision")
            try:
                result = await func(self, *args, **kwargs)
                logger.info(f"[{decision_type}] Decision completed: {result.get('status', 'unknown')}")
                return result
            except Exception as e:
                logger.error(f"[{decision_type}] Decision failed: {e}")
                raise
        
        return wrapper
    
    return decorator
