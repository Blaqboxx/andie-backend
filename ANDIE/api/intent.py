from fastapi import APIRouter
from pydantic import BaseModel
from core.autonomous_orchestrator import AutonomousOrchestrator

router = APIRouter()
andie = AutonomousOrchestrator()

class IntentRequest(BaseModel):
    text: str

@router.post("/intent")
def detect_intent(req: IntentRequest):
    text = req.text.lower()
    # Simple rule-based intent detection (replace with LLM for advanced)
    if "diagnostic" in text or "system check" in text:
        intent = {"type": "system_check", "priority": 9, "source": "voice", "payload": {}}
    elif "restart" in text and "node" in text:
        intent = {"type": "restart_node", "priority": 10, "source": "voice", "payload": {"target": text}}
    else:
        # Fallback: treat as conversation
        intent = {"type": "conversation", "priority": 6, "source": "voice", "payload": {"message": req.text}}
    # Route as a task
    result = andie.run_goal(intent["payload"].get("message", "")) if intent["type"] == "conversation" else andie.run_goal(str(intent))
    return {"intent": intent, "result": result}
