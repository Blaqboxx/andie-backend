from fastapi import FastAPI
from core.router import route_query

app = FastAPI()

@app.post("/ask")
def ask(data: dict):
    return {"response": route_query(data["query"])}
