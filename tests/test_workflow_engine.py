import unittest

from fastapi.testclient import TestClient

from interfaces.api.main import app
from interfaces.api.workflow_engine import workflow_engine


class WorkflowEngineTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_workflow_engine_runs_default_pipeline(self):
        result = workflow_engine.run_workflow("system audit workflow")

        self.assertEqual(result["workflow"], ["health_agent", "process_agent", "recovery_agent"])
        self.assertGreaterEqual(len(result["results"]), 1)
        self.assertIn(result["evaluation"]["status"], {"ok", "warning", "failed"})
        self.assertIn("response", result)

    def test_workflow_endpoint_returns_workflow_payload(self):
        response = self.client.post("/workflow/run", json={"task": "system audit workflow", "context": "test"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["task"], "system audit workflow")
        self.assertEqual(payload["workflow"], ["health_agent", "process_agent", "recovery_agent"])
        self.assertIn("evaluation", payload)

    def test_orchestrator_workflow_command_routes_to_engine(self):
        response = self.client.post("/orchestrator/run", json={"task": "run workflow for system audit", "context": "test"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "workflow")
        self.assertEqual(payload["status"], "started")
        self.assertEqual(payload["task"], "run workflow for system audit")
        self.assertEqual(payload["route"], "thinkpad")
        self.assertTrue(payload["result"]["streaming"])
        self.assertIn("workflowId", payload)


if __name__ == "__main__":
    unittest.main()
