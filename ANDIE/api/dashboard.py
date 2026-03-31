from core.global_sentinel import analyze_nodes
from core.goal_dependency_graph import GoalDependencyGraph
from fastapi import FastAPI
import json
import os

app = FastAPI()

METRICS_FILE = "node_metrics.json"

graph = GoalDependencyGraph()
# Example goals for demo (in real use, this would be managed by orchestrator)
fetch = graph.add_goal("fetch data", priority=2)
process = graph.add_goal("process data", priority=3, dependencies=[fetch])
visualize = graph.add_goal("visualize data", priority=4, dependencies=[process])

@app.get("/sentinel")
def sentinel_status():
    return analyze_nodes()

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

# New: Expose the current task dependency graph as JSON
@app.get("/tasks")
def get_tasks():
    def node_to_dict(node):
        return {
            "description": node.description,
            "priority": node.priority,
            "completed": node.completed,
            "dependencies": [dep.description for dep in node.dependencies]
        }
    return [node_to_dict(node) for node in graph.nodes]
