from pydantic import BaseModel
from typing import Optional, Dict, Any
import uuid
from datetime import datetime

# --- Command Model ---
class CommandRequest(BaseModel):
    text: str
    user_id: Optional[str] = "local"
    metadata: Optional[Dict[str, Any]] = {}

# --- Intent Classifier ---
def classify_intent(text: str) -> str:
    text = text.lower()
    if "build" in text:
        return "builder"
    elif "analyze" in text:
        return "analysis"
    elif "search" in text:
        return "research"
    else:
        return "general"

# --- Agent Base ---
class BaseAgent:
    name = "base"
    def run(self, command: CommandRequest):
        raise NotImplementedError

class BuilderAgent(BaseAgent):
    name = "builder"
    def run(self, command):
        return {"message": f"Building: {command.text}"}

class AnalysisAgent(BaseAgent):
    name = "analysis"
    def run(self, command):
        return {"message": f"Analyzing: {command.text}"}

class GeneralAgent(BaseAgent):
    name = "general"
    def run(self, command):
        return {"message": f"General handling: {command.text}"}

# --- Agent Registry ---
AGENTS = {
    "builder": BuilderAgent(),
    "analysis": AnalysisAgent(),
    "general": GeneralAgent(),
}

# --- Orchestrator Core ---
class Orchestrator:
    def handle(self, command: CommandRequest):
        # 1. Classify intent
        intent = classify_intent(command.text)
        # 2. Select agent
        agent = AGENTS.get(intent)
        if not agent:
            return {
                "status": "error",
                "message": f"No agent found for intent: {intent}"
            }
        # 3. Execute
        result = agent.run(command)
        # 4. Execution ID
        execution_id = str(uuid.uuid4())
        # 5. Logging
        print(f"[COMMAND] {datetime.now()} | {command.user_id} | {command.text} → {intent} [{execution_id}]")
        # 6. (Optional) Memory hook placeholder
        # memory.store(command.text, result)
        # 7. Return structured response
        return {
            "status": "success",
            "intent": intent,
            "agent": agent.name,
            "result": result,
            "execution_id": execution_id,
            "timestamp": datetime.utcnow().isoformat()
        }
