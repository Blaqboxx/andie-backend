import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

workspace_root = Path(__file__).resolve().parent.parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from interfaces.api.dispatcher import dispatch_task, get_best_node, score_node
from interfaces.api.self_healing.detector import detect_issues
from interfaces.api.self_healing.recovery import recover, recovery_task_for_issue


class NodeDispatchTests(unittest.TestCase):
    @patch("interfaces.api.dispatcher.check_node_health")
    def test_heavy_task_prefers_healthy_worker(self, check_node_health):
        check_node_health.return_value = {
            "thinkpad": {
                "available": True,
                "role": "brain",
                "metrics": {"loadPerCpu": 0.9, "memoryUsedPercent": 60, "latencyMs": 0},
                "executeUrl": "http://127.0.0.1:8000/orchestrator/run",
            },
            "nuc": {
                "available": True,
                "role": "worker",
                "metrics": {"loadPerCpu": 0.3, "memoryUsedPercent": 30, "latencyMs": 10},
                "executeUrl": "http://192.168.1.50:9000/execute",
            },
        }

        target = get_best_node(task_type="compute_heavy")

        self.assertEqual(target["node"], "nuc")
        self.assertEqual(target["reason"], "compute_heavy_lowest_score")

    @patch("interfaces.api.dispatcher.check_node_health")
    def test_heavy_task_falls_back_to_thinkpad_when_worker_offline(self, check_node_health):
        check_node_health.return_value = {
            "thinkpad": {
                "available": True,
                "role": "brain",
                "metrics": {"loadPerCpu": 0.9, "memoryUsedPercent": 60, "latencyMs": 0},
                "executeUrl": "http://127.0.0.1:8000/orchestrator/run",
            },
            "nuc": {
                "available": False,
                "role": "worker",
                "metrics": {"loadPerCpu": 0.2, "memoryUsedPercent": 20, "latencyMs": 10},
                "executeUrl": "http://192.168.1.50:9000/execute",
            },
        }

        dispatch = dispatch_task("deep scan repo", task_type="compute_heavy")

        self.assertEqual(dispatch["targetNode"], "thinkpad")

    def test_score_node_penalizes_brain_for_compute_heavy(self):
        thinkpad_score = score_node(
            {"role": "brain", "metrics": {"loadPerCpu": 0.4, "memoryUsedPercent": 40, "latencyMs": 0}, "overloaded": False},
            task_type="compute_heavy",
        )
        worker_score = score_node(
            {"role": "worker", "metrics": {"loadPerCpu": 0.4, "memoryUsedPercent": 40, "latencyMs": 10}, "overloaded": False},
            task_type="compute_heavy",
        )

        self.assertLess(worker_score, thinkpad_score)

    @patch("interfaces.api.dispatcher.check_node_health")
    def test_low_latency_prefers_thinkpad_with_lower_latency(self, check_node_health):
        check_node_health.return_value = {
            "thinkpad": {
                "available": True,
                "role": "brain",
                "metrics": {"loadPerCpu": 0.7, "memoryUsedPercent": 50, "latencyMs": 0},
                "executeUrl": "http://127.0.0.1:8000/orchestrator/run",
            },
            "nuc": {
                "available": True,
                "role": "worker",
                "metrics": {"loadPerCpu": 0.3, "memoryUsedPercent": 20, "latencyMs": 22},
                "executeUrl": "http://192.168.1.50:9000/execute",
            },
        }

        target = get_best_node(task_type="low_latency")

        self.assertEqual(target["node"], "thinkpad")


class NodeFailureDetectorTests(unittest.TestCase):
    def test_detector_emits_node_failure_for_offline_worker(self):
        state = {
            "queue": 0,
            "queueDelta": 0,
            "cpu": 10,
            "nodes": {
                "thinkpad": {"role": "brain", "status": "healthy"},
                "nuc": {"role": "worker", "status": "offline"},
            },
        }

        issues = detect_issues(state, [])

        self.assertIn({"type": "node_failure", "node": "nuc", "role": "worker", "status": "offline"}, issues)

    def test_recovery_task_for_node_failure_reroutes(self):
        self.assertEqual(recovery_task_for_issue({"type": "node_failure", "node": "nuc"}), "reroute tasks from nuc to thinkpad")


class NodeFailureRecoveryTests(unittest.IsolatedAsyncioTestCase):
    @patch("interfaces.api.self_healing.recovery.reroute_tasks")
    async def test_node_failure_recovery_reroutes_queue(self, reroute_tasks):
        reroute_tasks.return_value = {"source": "nuc", "target": "thinkpad", "rerouted": 2}

        result = await recover({"type": "node_failure", "node": "nuc"}, iteration=1, state={"nodes": {}})

        self.assertEqual(result["status"], "recovery_started")
        self.assertEqual(result["workflow"]["result"]["rerouted"], 2)


if __name__ == "__main__":
    unittest.main()