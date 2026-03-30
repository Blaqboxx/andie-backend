from fastapi import FastAPI, Request
from pydantic import BaseModel
from valhalla.controller.sandbox_manager import run_in_sandbox

app = FastAPI()

class RunRequest(BaseModel):
    code: str

@app.post("/run")
def run_code(req: RunRequest):
    result = run_in_sandbox(req.code)
    return result

@app.get("/health")
def health():
    return {"status": "ok"}
