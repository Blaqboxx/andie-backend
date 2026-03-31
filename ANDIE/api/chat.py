from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core.sentinel import validate_message
from core.autonomous_orchestrator import AutonomousOrchestrator
import asyncio

router = APIRouter()
andie = AutonomousOrchestrator()

@router.websocket("/ws/chat")
async def chat_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "CHAT":
                user_msg = data["message"]
                # Sentinel validation
                allowed, reason = validate_message(user_msg)
                if not allowed:
                    await ws.send_json({"type": "SENTINEL_ALERT", "message": reason})
                    continue
                # Streaming callback
                async def stream_callback(token):
                    await ws.send_json({"type": "CHAT_STREAM", "chunk": token})
                # Route as a conversation task
                task = {
                    "type": "conversation",
                    "priority": 6,
                    "source": "chat_ui",
                    "payload": {"message": user_msg},
                    "stream": stream_callback
                }
                # submit() is now async for streaming
                await andie.submit(task)
    except WebSocketDisconnect:
        pass
