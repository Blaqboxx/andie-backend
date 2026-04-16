import unittest
import os
import tempfile

from fastapi.testclient import TestClient

from andie_core.agents.frontend_ui_agent import run_agent
from interfaces.api.main import app
from worker.worker_runtime import execute_worker_payload


class FrontendUIAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_queue_path = os.environ.get("ANDIE_QUEUE_PATH")
        os.environ["ANDIE_QUEUE_PATH"] = os.path.join(self.temp_dir.name, "queue.json")
        self.addCleanup(self.restore_queue_path)
        self.client = TestClient(app)

    def restore_queue_path(self):
        if self.original_queue_path is None:
            os.environ.pop("ANDIE_QUEUE_PATH", None)
        else:
            os.environ["ANDIE_QUEUE_PATH"] = self.original_queue_path

    def test_frontend_ui_agent_builds_issue_brief(self):
        result = run_agent(
            {
                "prompt": "Fix the frontend UI issue.",
                "context": "Vite on 127.0.0.1:5173 is unstable and the Why This Node panel needs verification.",
                "metadata": {"files": ["andie-ui/src/pages/Dashboard.jsx"]},
            }
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["agent"], "frontend_ui_agent")
        self.assertEqual(result["targetFiles"], ["andie-ui/src/pages/Dashboard.jsx"])
        self.assertIn("Why This Node", " ".join(result["successCriteria"]))

    def test_agent_endpoint_resolves_short_frontend_ui_agent_name(self):
        response = self.client.post(
            "/agent/frontend_ui_agent",
            json={
                "input": "Investigate the dashboard frontend issue.",
                "params": {
                    "context": "The Vite dev server is dropping and the decision trace panel needs verification.",
                    "metadata": {"files": ["andie-ui/package.json", "andie-ui/src/pages/Dashboard.jsx"]},
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "executed")
        self.assertEqual(payload["result"]["agent"], "frontend_ui_agent")
        self.assertIn("Vite", " ".join(payload["result"]["executionPlan"]))

    def test_frontend_issue_endpoint_queues_frontend_issue_task(self):
        response = self.client.post(
            "/frontend/issues",
            json={
                "issue": "Fix the unstable frontend UI.",
                "context": "Dashboard verification depends on a stable dev path.",
                "files": ["andie-ui/src/pages/Dashboard.jsx"],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["task"]["type"], "frontend_issue")
        self.assertEqual(payload["task"]["preferredNode"], "thinkpad")
        self.assertEqual(payload["task"]["payload"]["files"], ["andie-ui/src/pages/Dashboard.jsx"])

    def test_worker_runtime_executes_frontend_issue_task(self):
        result = execute_worker_payload(
            {
                "id": 42,
                "type": "frontend_issue",
                "payload": {
                    "issue": "Fix the Vite transport instability.",
                    "context": "Why This Node must be visible after the fix.",
                    "files": ["andie-ui/src/pages/Dashboard.jsx"],
                },
            }
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["taskType"], "frontend_issue")
        self.assertEqual(result["result"]["agent"], "frontend_ui_agent")
        self.assertIn("Why This Node", " ".join(result["result"]["successCriteria"]))


if __name__ == "__main__":
    unittest.main()