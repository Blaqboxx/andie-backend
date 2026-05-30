import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if "andie_backend" in sys.modules:
    loaded = sys.modules.get("andie_backend")
    loaded_file = str(getattr(loaded, "__file__", "") or "")
    loaded_paths = [str(path) for path in (getattr(loaded, "__path__", []) or [])]
    in_repo = (str(REPO_ROOT) in loaded_file) or any(str(REPO_ROOT) in path for path in loaded_paths)
    if not in_repo:
        for key in list(sys.modules.keys()):
            if key == "andie_backend" or key.startswith("andie_backend."):
                sys.modules.pop(key, None)
        importlib.invalidate_caches()

import andie_backend
expected_paths = [str(REPO_ROOT / "andie_backend"), str(REPO_ROOT)]
current_paths = [str(path) for path in (getattr(andie_backend, "__path__", []) or [])]
for candidate in expected_paths:
    if candidate not in current_paths:
        current_paths.append(candidate)
andie_backend.__path__ = current_paths

from andie_backend.trading.orchestrator import run_capital_orchestration
from interfaces.api.main import app


class CapitalOrchestrationTests(unittest.TestCase):
    @patch("andie_backend.trading.orchestrator.run_data_agent")
    def test_orchestrator_cycle_tracks_deposit_and_risk(self, mock_data):
        mock_data.return_value = {
            "status": "ok",
            "query": {"symbol": "BTC"},
            "series": [
                {"close": 100},
                {"close": 101},
                {"close": 102},
                {"close": 103},
                {"close": 104},
                {"close": 105},
            ],
        }

        result = run_capital_orchestration(
            {
                "current_balance": 1000,
                "deposit_amount": 200,
                "monthly_deposit": 100,
                "symbol": "BTC",
                "timeframe": "1h",
                "risk_level": "moderate",
            }
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "capital_orchestration_cycle")
        self.assertIn("cycle", result)
        self.assertIn("capital", result["cycle"])
        self.assertGreater(result["cycle"]["capital"]["total_balance"], 1000)
        self.assertIn("execution", result["cycle"])
        self.assertIn("allocated_risk_usd", result["cycle"]["execution"])

    @patch("andie_backend.trading.orchestrator.run_data_agent")
    def test_api_endpoint_runs_capital_cycle(self, mock_data):
        mock_data.return_value = {
            "status": "ok",
            "query": {"symbol": "BTC"},
            "series": [
                {"close": 100},
                {"close": 101},
                {"close": 102},
                {"close": 103},
                {"close": 104},
                {"close": 105},
            ],
        }

        client = TestClient(app)
        response = client.post(
            "/cryptonia/capital/orchestrate",
            json={
                "current_balance": 1500,
                "monthly_deposit": 100,
                "deposit_amount": 100,
                "symbol": "BTC",
                "timeframe": "1h",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["mode"], "capital_orchestration_cycle")
        self.assertIn("cycle", payload)


if __name__ == "__main__":
    unittest.main()
