import json

def log_metrics(metrics):
    with open("node_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
