"""Memory agent — surfaces stored memory records passed in via context."""
from typing import Any, Dict, List, Optional


async def run(message: str, context: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """
    The context list IS the memory results — the orchestrator already ran
    `memory.search(message)` before calling this agent, so we just surface
    those entries to the user.
    """
    entries = context or []

    # Build a readable summary of past interactions
    if entries:
        lines = []
        for e in entries:
            ts  = e.get("timestamp", "")
            inp = e.get("input",     e.get("content", ""))
            agt = e.get("agent",     "?")
            lines.append(f"[{agt}] {inp!r}")
        summary = "Found related history: " + " | ".join(lines)
    else:
        summary = "No matching memory records found for this query."

    return {
        "agent":        "memory_agent",
        "response":     summary,
        "results":      entries,
        "summary":      summary,
        "message":      message,
        "context_used": len(entries),
    }
