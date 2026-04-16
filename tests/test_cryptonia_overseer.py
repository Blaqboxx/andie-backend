import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from andie_core.agents.cryptonia_strategy_agent import run_agent as run_strategy_agent
from interfaces.api.main import app


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class CryptoniaOverseerTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.original_key = os.environ.get("COINMARKETCAP_API_KEY")

    def tearDown(self):
        if self.original_key is None:
            os.environ.pop("COINMARKETCAP_API_KEY", None)
        else:
            os.environ["COINMARKETCAP_API_KEY"] = self.original_key

    def test_strategy_agent_analyzes_market_data(self):
        payload = {
            "metadata": {
                "constraints": {"risk_level": "moderate", "timeframe": "long_term"},
                "market_data": {
                    "series": [
                        {"close": 100},
                        {"close": 102},
                        {"close": 104},
                        {"close": 107},
                        {"close": 110},
                        {"close": 112},
                        {"close": 115},
                    ]
                },
            }
        }

        result = run_strategy_agent(payload)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["agent"], "cryptonia_strategy_agent")
        self.assertIn("confidence", result)
        self.assertIn("risk_score", result)
        self.assertIn("action", result)

    def test_capabilities_endpoint_exposes_crypto_runtime_contract(self):
        response = self.client.get("/agents/capabilities")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("capabilities", payload)
        self.assertIn("crypto_data", payload["capabilities"])
        self.assertIn("crypto_strategy", payload["capabilities"])
        self.assertEqual(sorted(payload["allowedActiveCapabilities"]), ["crypto_data", "crypto_strategy"])

    @patch("andie_core.agents.coinmarketcap_agent.requests.get")
    def test_overseer_endpoint_runs_dual_agent_flow(self, mock_get):
        os.environ["COINMARKETCAP_API_KEY"] = "test-key"

        quotes = []
        closes = [100, 101, 102, 103, 104, 106, 108, 110, 112, 114]
        for idx, close in enumerate(closes):
            quotes.append(
                {
                    "timestamp": f"2025-01-{idx + 1:02d}T00:00:00.000Z",
                    "quote": {
                        "USD": {
                            "open": close - 1,
                            "high": close + 1,
                            "low": close - 2,
                            "close": close,
                            "volume": 1000 + idx,
                            "market_cap": 100000 + idx,
                        }
                    },
                }
            )

        mock_get.return_value = _FakeResponse(status_code=200, payload={"data": {"quotes": quotes}})

        response = self.client.post(
            "/cryptonia/overseer/run",
            json={
                "task": "analyze BTC long-term trend",
                "profile": "balanced",
                "data_agent": "cryptonia_historical_agent",
                "strategy_agent": "cryptonia_strategy_agent",
                "constraints": {
                    "risk_level": "moderate",
                    "timeframe": "long_term",
                    "confidence_threshold": 0.6,
                    "max_risk_score": 0.7,
                    "min_data_quality": 0.5,
                },
                "metadata": {
                    "symbol": "BTC",
                    "start": "2025-01-01",
                    "end": "2025-01-10",
                    "interval": "daily",
                    "convert": "USD",
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["mode"], "dual_agent_overseer")
        self.assertEqual(payload["agentResolution"]["data"]["resolved"], "coinmarketcap_agent")
        self.assertEqual(payload["agentResolution"]["strategy"]["resolved"], "cryptonia_strategy_agent")
        self.assertEqual(sorted(payload["activeCapabilities"]), ["crypto_data", "crypto_strategy"])
        self.assertEqual(payload["data"]["normalized"]["type"], "market_data")
        self.assertEqual(payload["strategy"]["normalized"]["type"], "strategy")
        self.assertIn("andieDecision", payload)
        self.assertEqual(payload["andieDecision"]["profile"], "balanced")
        self.assertIn("composite_score", payload["andieDecision"])
        self.assertIn("risk_adjusted", payload["andieDecision"])
        self.assertIn("weights", payload["andieDecision"])
        self.assertIn("signals", payload["andieDecision"])
        self.assertIn("reason_trace", payload["andieDecision"])
        self.assertIn(payload["andieDecision"]["execution"], ["buy_strong", "buy", "accumulate_small", "hold", "wait"])
        self.assertIn("composite_score", payload["evaluation"])
        self.assertIn("weights", payload["evaluation"])
        self.assertIn("profile", payload["evaluation"])
        self.assertIn("reason_trace", payload["evaluation"])
        self.assertIn(payload["evaluation"]["decision"], ["approve", "hold"])

    def test_overseer_rejects_non_trading_capability_pair(self):
        response = self.client.post(
            "/cryptonia/overseer/run",
            json={
                "task": "analyze BTC trend",
                "data_capability": "system_health",
                "strategy_capability": "crypto_strategy",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("exactly two active capabilities", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
