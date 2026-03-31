from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from ANDIE.api.chat import router as chat_router
from ANDIE.api.memory import router as memory_router
from ANDIE.api.dashboard import app as dashboard_app
from ANDIE.api.command import router as command_router

app = FastAPI()

# 🚨 REQUIRED for mobile connection
app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],  # tighten later
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(command_router)
# Intent router for voice command mode
from ANDIE.api.intent import router as intent_router
app.include_router(intent_router)
# Mount dashboard endpoints
app.mount("/dashboard", dashboard_app)
