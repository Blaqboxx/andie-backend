import unittest

from fastapi.testclient import TestClient

from autonomy.explainer import remember_decision_context
from interfaces.api.main import app


class AutonomyExplainerApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_explain_returns_last_decision_context(self):
        remember_decision_context(
            {
                "decision": "REVIEW",
                "confidence": 0.58,
                "trust": 0.71,
                "plan": [
                    {"step": "validate_signal", "why": "ensure input is valid"},
                    {"step": "check_risk", "why": "avoid unsafe execution"},
                ],
                "knowledge_guidance": {"answer": "Momentum signals require volume confirmation.", "relevant": True},
                "multi_agent_plan": [
                    {"agent": "signal_agent", "task": "validate price signal"},
                ],
            }
        )

        response = self.client.get("/autonomy/explain")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["decision"], "REVIEW")
        self.assertEqual(len(payload["reasoning"]), 2)
        self.assertEqual(payload["knowledge"]["answer"], "Momentum signals require volume confirmation.")
