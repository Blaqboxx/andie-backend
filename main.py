from fastapi import FastAPI
from pydantic import BaseModel
from groq import Groq
from dotenv import load_dotenv
import os
import json
# Load environment variables
load_dotenv()

# Initialize app
app = FastAPI()

# Validate API key early
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("❌ GROQ_API_KEY is missing. Check your .env file.")

client = Groq(api_key=GROQ_API_KEY)

# Request model (IMPORTANT)
class AgentRequest(BaseModel):
    task: str

# Memory system
@app.post("/agents/run")
def run_agent(request: AgentRequest):
    try:
        memory = load_memory()

        memory.append({
            "role": "user",
            "content": request.task
        })

        response = client.chat.completions.create(
            messages=memory,
            model="llama-3.1-8b-instant"
        )

        reply = response.choices[0].message.content

        memory.append({
            "role": "assistant",
            "content": reply
        })

        # Keep last 20 messages
        memory = memory[-20:]

        save_memory(memory)

        return {
            "result": reply,
            "memory_size": len(memory)
        }

    except Exception as e:
        return {"error": str(e)}

# Health check route
@app.get("/")
def home():
    return {"status": "ANDIE backend running"}

# Agent endpoint
@app.post("/agents/run")
def run_agent(request: AgentRequest):
    try:
        task = request.task

        # Store user input
        conversation_history.append({
            "role": "user",
            "content": task
        })

        # AI call
        response = client.chat.completions.create(
            messages=conversation_history,
            model="llama-3.1-8b-instant"
        )

        reply = response.choices[0].message.content

        # Store AI response
        conversation_history.append({
            "role": "assistant",
            "content": reply
        })

        return {
            "result": reply,
            "memory_size": len(conversation_history)
        }

    except Exception as e:
        return {"error": str(e)}
