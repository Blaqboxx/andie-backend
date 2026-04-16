import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient

from andie_core.agents.coinmarketcap_agent import run_agent
from interfaces.api.main import app


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class CoinMarketCapAgentTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.original_key = os.environ.get("COINMARKETCAP_API_KEY")

    def tearDown(self):
        if self.original_key is None:
            os.environ.pop("COINMARKETCAP_API_KEY", None)
        else:
            os.environ["COINMARKETCAP_API_KEY"] = self.original_key

    def test_run_agent_requires_api_key(self):
        os.environ.pop("COINMARKETCAP_API_KEY", None)
        os.environ.pop("CMC_API_KEY", None)

        result = run_agent({"prompt": "Get BTC historical data"})
        self.assertEqual(result["status"], "error")
        self.assertIn("COINMARKETCAP_API_KEY", result["error"])

    @patch("andie_core.agents.coinmarketcap_agent.requests.get")
    def test_run_agent_returns_series(self, mock_get):
        os.environ["COINMARKETCAP_API_KEY"] = "test-key"
        mock_get.return_value = _FakeResponse(
            status_code=200,
            payload={
                "data": {
                    "quotes": [
                        {
                            "timestamp": "2025-01-01T00:00:00.000Z",
                            "quote": {
                                "USD": {
                                    "open": 100.0,
                                    "high": 110.0,
                                    "low": 95.0,
                                    "close": 105.0,
                                    "volume": 1234.0,
                                    "market_cap": 9999.0,
                                }
                            },
                        }
                    ]
                }
            },
        )

        result = run_agent({"prompt": "Get $BTC historical data from 2025-01-01 to 2025-01-31"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["agent"], "coinmarketcap_agent")
        self.assertEqual(result["query"]["symbol"], "BTC")
        self.assertEqual(result["points"], 1)
        self.assertEqual(result["last"]["close"], 105.0)

    @patch("andie_core.agents.coinmarketcap_agent.requests.get")
    def test_endpoint_executes_coinmarketcap_agent(self, mock_get):
        os.environ["COINMARKETCAP_API_KEY"] = "test-key"
        mock_get.return_value = _FakeResponse(status_code=200, payload={"data": {"quotes": []}})

        response = self.client.post(
            "/agent/coinmarketcap_agent",
            json={
                "input": "Get BTC historical daily data from 2025-01-01 to 2025-01-10",
                "params": {
                    "metadata": {
                        "symbol": "BTC",
                        "interval": "daily",
                        "start": "2025-01-01",
                        "end": "2025-01-10",
                    }
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "executed")
        self.assertEqual(payload["result"]["agent"], "coinmarketcap_agent")

    @patch("andie_core.agents.coinmarketcap_agent.requests.get")
    def test_cryptonia_alias_executes_same_agent(self, mock_get):
        os.environ["COINMARKETCAP_API_KEY"] = "test-key"
        mock_get.return_value = _FakeResponse(status_code=200, payload={"data": {"quotes": []}})

        response = self.client.post(
            "/agent/cryptonia_historical_agent",
            json={
                "input": "Get ETH historical daily data from 2025-01-01 to 2025-01-10",
                "params": {
                    "metadata": {
                        "symbol": "ETH",
                        "interval": "daily",
                        "start": "2025-01-01",
                        "end": "2025-01-10",
                    }
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "executed")
        self.assertEqual(payload["result"]["agent"], "coinmarketcap_agent")
        self.assertEqual(payload["agentResolution"]["requested"], "cryptonia_historical_agent")
        self.assertEqual(payload["agentResolution"]["resolved"], "coinmarketcap_agent")

    def test_aliases_endpoint_lists_cryptonia_alias(self):
        response = self.client.get("/agents/aliases")
        self.assertEqual(response.status_code, 200)
        aliases = response.json()
        self.assertEqual(aliases["cryptonia_historical_agent"], "coinmarketcap_agent")


if __name__ == "__main__":
    unittest.main()
