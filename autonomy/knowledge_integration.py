"""
Knowledge Integration for Autonomy
Provides utilities to query knowledge and enrich autonomous decision contexts.
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KNOWLEDGE_API_URL = "http://127.0.0.1:8000/knowledge/answer"


def query_knowledge_for_event(
    event_type: str,
    event_description: str,
    context_data: Optional[Dict[str, Any]] = None,
    mode: str = "answer",
    timeout_seconds: float = 2.0,
) -> Optional[Dict[str, Any]]:
    """
    Query the knowledge system for guidance on a specific event type.
    
    Args:
        event_type: Type of event (e.g., "stream_recovery", "price_alert")
        event_description: Detailed description of the current event
        context_data: Additional context for knowledge synthesis
        mode: Answer synthesis mode ("answer", "explain", "summarize")
        timeout_seconds: HTTP request timeout
        
    Returns:
        Knowledge answer dict with "answer", "sources", "status" or None if unavailable
    """
    try:
        # Build query combining event type and description
        query = f"{event_type}: {event_description}".strip()
        
        payload = {
            "query": query,
            "mode": mode,
            "k": 3,  # Retrieve top 3 relevant chunks
        }
        
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            KNOWLEDGE_API_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            if 200 <= response.status < 300:
                reply = json.loads(response.read().decode("utf-8"))
                return reply
            else:
                logger.warning(f"Knowledge API returned status {response.status}")
                return None
                
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
        logger.debug(f"Knowledge query failed for event {event_type}: {e}")
        return None


def enrich_autonomy_context(
    context: Dict[str, Any],
    knowledge_enabled: bool = True,
) -> Dict[str, Any]:
    """
    Enrich an autonomy context with knowledge guidance.
    
    Args:
        context: Standard autonomy context dict with 'event' key
        knowledge_enabled: Whether to query knowledge (can be disabled via env var)
        
    Returns:
        Enhanced context dict with 'knowledge_guidance' key if enrichment succeeds
    """
    if not knowledge_enabled:
        return context
        
    event = context.get("event")
    if not isinstance(event, dict):
        return context
        
    event_type = event.get("type", "unknown")
    event_desc = event.get("description", "")
    
    if not event_type or event_type == "unknown":
        return context
    
    # Query knowledge for this event type
    answer_data = query_knowledge_for_event(
        event_type=event_type,
        event_description=event_desc,
        mode="explain",
        timeout_seconds=1.5,  # Keep autonomous decisions fast
    )
    
    if answer_data and answer_data.get("status") == "ok":
        # Add knowledge guidance to context
        context["knowledge_guidance"] = {
            "answer": answer_data.get("answer"),
            "sources": answer_data.get("sources", []),
            "results": answer_data.get("results", []),
            "relevant": True,
        }
    else:
        context["knowledge_guidance"] = {
            "answer": None,
            "sources": [],
            "results": [],
            "relevant": False,
        }
    
    return context


def build_decision_rationale(
    decision: str,
    context: Dict[str, Any],
) -> str:
    """
    Build a detailed rationale for an autonomous decision.
    
    Args:
        decision: The decision being made (e.g., "execute_trade", "block_trade")
        context: Autonomy context potentially enriched with knowledge
        
    Returns:
        Human-readable rationale string
    """
    rationale_parts = [f"Decision: {decision}"]
    
    # Add knowledge-based reasoning if available
    knowledge = context.get("knowledge_guidance")
    if knowledge and knowledge.get("relevant"):
        rationale_parts.append(f"Knowledge-guided: {knowledge.get('answer', 'N/A')[:100]}...")
    
    # Add risk analysis if trading
    metadata = context.get("event", {}).get("metadata", {})
    if metadata.get("dailyLoss") or metadata.get("openPositions"):
        rationale_parts.append(
            f"Risk check: daily_loss={metadata.get('dailyLoss', 0)}, "
            f"open_positions={metadata.get('openPositions', 0)}"
        )
    
    return " | ".join(rationale_parts)


def is_knowledge_query_disabled() -> bool:
    """Check if knowledge enrichment is disabled by environment."""
    import os
    return os.environ.get("ANDIE_AUTONOMY_DISABLE_KNOWLEDGE", "").lower() == "true"
