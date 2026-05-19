"""Fallback agent — catches any unrouted messages."""
from typing import Any, Dict, List, Optional


async def run(message: str, context: Optional[List[Dict]] = None) -> Dict[str, Any]:
    text = message.lower()
    if any(word in text for word in ["hello", "hi", "hey", "yo"]):
        response = f"Hey 👋 I got your message: '{message}'. How can I help you?"
    else:
        response = f"I'm here — just tell me what you want to do 👍 (You said: '{message}')"
    return {
        "agent": "fallback_agent",
        "response": response,
        "message": message,
        "context_used": len(context) if context else 0,
    }
