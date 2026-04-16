import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from interfaces.api.main import app
from interfaces.api.trading_approvals import clear_trade_approvals


class TradingApprovalsApiTests(unittest.TestCase):
    def setUp(self):
        clear_trade_approvals()
        self.client = TestClient(app)

    def _create_pending_approval(self) -> str:
        response = self.client.post(
            "/events/publish",
            json={
                "type": "APPROVAL_REQUIRED",
                "status": "pending",
                "target": "trading",
                "message": "Approve trade",
                "metadata": {
                    "trade": {
                        "symbol": "BTC/USDT",
                        "action": "buy",
                        "price": 70000,
                    },
                    "dryRun": True,
                    "tradingMode": "SEMI_AUTO",
                },
            },
        )
        self.assertEqual(response.status_code, 200)

        pending = self.client.get("/trading/approvals").json()["items"]
        self.assertEqual(len(pending), 1)
        return pending[0]["approvalId"]

    def test_reject_pending_approval(self):
        approval_id = self._create_pending_approval()

        response = self.client.post(
            f"/trading/approvals/{approval_id}/reject",
            json={"actor": "operator", "reason": "not_now"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["approval"]["status"], "rejected")

    def test_approve_pending_approval_executes_trade_path(self):
        approval_id = self._create_pending_approval()

        with patch(
            "interfaces.api.main.execute_approved_trade",
            new=AsyncMock(return_value={"status": "ok", "execution": {"status": "simulated"}}),
        ) as execute_trade:
            response = self.client.post(
                f"/trading/approvals/{approval_id}/approve",
                json={"actor": "operator", "metadata": {"dryRun": True}},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "approved")
        self.assertEqual(payload["approval"]["status"], "approved")
        execute_trade.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
