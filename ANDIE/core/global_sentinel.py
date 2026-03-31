import json
import os

METRICS_FILE = "node_metrics.json"

FAIL_THRESHOLD = 5
LATENCY_THRESHOLD = 500

def load_metrics():
    if not os.path.exists(METRICS_FILE):
        return {}
    with open(METRICS_FILE, "r") as f:
        return json.load(f)

def calculate_reputation(m):
    total = m["success"] + m["fail"]
    if total == 0:
        return 1.0
    success_rate = m["success"] / total
    latency_penalty = min(m["latency"] / 1000, 1)
    return round(success_rate - latency_penalty, 2)

def analyze_nodes():
    metrics = load_metrics()
    decisions = {}
    for node, m in metrics.items():
        status = "healthy"
        if m["fail"] >= FAIL_THRESHOLD:
            status = "blocked"
        elif m["latency"] > LATENCY_THRESHOLD:
            status = "degraded"
        reputation = calculate_reputation(m)
        decisions[node] = {
            "status": status,
            "reputation": reputation,
            "metrics": m
        }
    return decisions

def detect_anomalies(metrics):
    alerts = []
    for node, m in metrics.items():
        if m["fail"] > m["success"]:
            alerts.append(f"{node} unstable")
        if m["latency"] > 1000:
            alerts.append(f"{node} high latency spike")
    return alerts


# Self-healing: generate repair goals based on anomalies
def generate_repair_goals():
    metrics = load_metrics()
    anomalies = detect_anomalies(metrics)
    repair_goals = []
    for alert in anomalies:
        if "unstable" in alert:
            node = alert.split()[0]
            repair_goals.append(f"Restart or isolate node {node} due to instability.")
        if "latency spike" in alert:
            node = alert.split()[0]
            repair_goals.append(f"Investigate high latency on node {node}.")
    return repair_goals
