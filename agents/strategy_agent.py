"""Strategy agent — returns structured trading/planning strategy responses."""
from typing import Any, Dict, List, Optional


async def run(message: str, context: Optional[List[Dict]] = None) -> Dict[str, Any]:
    # Use context to note if a similar strategy was discussed before
    prior_strategies = [
        e for e in (context or [])
        if e.get("agent") == "strategy_agent"
    ]
    prior_note = (
        f" (based on {len(prior_strategies)} prior strategy session(s))"
        if prior_strategies else ""
    )
    return {
        "agent": "strategy_agent",
        "response": f"Strategy analysis for: {message}{prior_note}",
        "recommendation": "Hold — insufficient signal confidence",
        "confidence": 0.55,
        "risk_level": "medium",
        "message": message,
        "context_used": len(prior_strategies),
    }
