def compute_health_score(cpu, memory, llm_active):
def classify(score):

# Decision logic is now handled by MCP. Use get_health_status to call MCP.
import httpx
import asyncio

async def get_health_status(cpu, memory, llm_active):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:7001/health/analyze",
            json={
                "cpu": cpu,
                "memory": memory,
                "llm_active": llm_active
            }
        )
        return response.json()
