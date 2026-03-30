
import random
import requests
import time
import math

class NodeScheduler:
    def __init__(self, nodes):
        self.nodes = nodes
        self.metrics = {node: {"success": 1, "fail": 0, "latency": 100.0} for node in nodes}

    def check_node(self, node_url):
        try:
            start = time.time()
            r = requests.get(f"{node_url}/health", timeout=2)
            latency = time.time() - start
            healthy = r.status_code == 200
            if healthy:
                self.update_metrics(node_url, {"status": "SUCCESS"}, latency)
            return healthy
        except Exception:
            return False

    def is_node_available(self, node):
        m = self.metrics.get(node, {})
        # Circuit breaker: disable node if 5+ consecutive fails
        if m.get("fail", 0) >= 5:
            return False
        return True

    def get_healthy_nodes(self):
        return [n for n in self.nodes if self.check_node(n) and self.is_node_available(n)]

    def _score(self, node):
        m = self.metrics.get(node, {"success": 1, "fail": 0, "latency": 100})
        total = m["success"] + m["fail"]
        failure_rate = m["fail"] / total if total else 0
        success_rate = m["success"] / total if total else 1
        latency = m["latency"]
        # weights (tune as needed)
        return (0.6 * latency) + (0.3 * failure_rate * 100) - (0.1 * success_rate * 100)

    def select_node(self):
        healthy = self.get_healthy_nodes()
        if not healthy:
            return None
        scored = [(self._score(n), n) for n in healthy]
        scored.sort(key=lambda x: x[0])
        return scored[0][1]

    def update_metrics(self, node, result, latency):
        if node not in self.metrics:
            self.metrics[node] = {"success": 0, "fail": 0, "latency": latency}
        if result.get("status") == "SUCCESS":
            self.metrics[node]["success"] += 1
        else:
            self.metrics[node]["fail"] += 1
        # rolling latency average
        self.metrics[node]["latency"] = (self.metrics[node]["latency"] + latency) / 2
