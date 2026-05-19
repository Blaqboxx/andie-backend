from typing import Any, Dict, List, Optional


async def run(message: str, context: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Example crypto agent — returns mock market data."""
    return {
        "agent": "crypto_agent",
        "response": "mock crypto data",
        "message": message,
        "context_used": len(context) if context else 0,
    }
