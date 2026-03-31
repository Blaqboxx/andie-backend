from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict, Any
from ANDIE.core.orchestrator import Orchestrator, CommandRequest as OrchestratorCommandRequest

router = APIRouter()
orchestrator = Orchestrator()

# Accepts: {"text": ..., "user_id": ..., "metadata": ...}
@router.post("/command")
async def handle_command(req: Dict[str, Any]):
    # Convert to orchestrator's CommandRequest
    cmd = OrchestratorCommandRequest(**req)
    response = orchestrator.handle(cmd)
    return response
