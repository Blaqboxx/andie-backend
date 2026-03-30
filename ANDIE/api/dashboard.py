from fastapi import FastAPI
import json
import os

app = FastAPI()

METRICS_FILE = "node_metrics.json"

@app.get("/metrics")
def get_metrics():
    if not os.path.exists(METRICS_FILE):
        return {}
    with open(METRICS_FILE, "r") as f:
        return json.load(f)

@app.get("/status")
def system_status():
    return {
        "status": "running",
        "message": "ANDIE Dashboard Active"
    }
