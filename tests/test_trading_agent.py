import os
import unittest
from unittest.mock import patch

from autonomy import trading_agent


class TradingAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        trading_agent._RECENT_SIGNALS.clear()

    async def test_safe_mode_requires_approval(self):
        context = {
            "event": {
                "type": "TRADE_SIGNAL",
                "action": "buy",
                "metadata": {
                    "symbol": "BTC/USDT",
                    "signal": "buy",
                    "price": 70000,
                    "tradingMode": "SAFE",
                    "dryRun": True,
                },
            }
        }

        with patch("autonomy.trading_agent._publish_event", return_value=True) as publish:
            result = await trading_agent.run_agent(context)

        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(result["mode"], "SAFE")
        self.assertGreaterEqual(publish.call_count, 1)
        self.assertEqual(publish.call_args_list[-1].args[0]["type"], "APPROVAL_REQUIRED")

    async def test_auto_mode_executes_dry_run_trade(self):
        context = {
            "event": {
                "type": "TRADE_SIGNAL",
                "action": "buy",
                "metadata": {
                    "symbol": "BTC/USDT",
                    "signal": "buy",
                    "price": 70000,
                    "tradingMode": "AUTO",
                    "dryRun": True,
                    "openPositions": 0,
                    "dailyLoss": 0,
                },
            }
        }

        with patch("autonomy.trading_agent._publish_event", return_value=True):
            result = await trading_agent.run_agent(context)

        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(result["mode"], "AUTO")
        self.assertEqual(result["decision"], "REVIEW")

    async def test_duplicate_signal_is_blocked(self):
        context = {
            "event": {
                "type": "TRADE_SIGNAL",
                "action": "sell",
                "metadata": {
                    "symbol": "ETH/USDT",
                    "signal": "sell",
                    "price": 3500,
                    "tradingMode": "AUTO",
                    "dryRun": True,
                    "openPositions": 0,
                    "dailyLoss": 0,
                },
            }
        }

        os.environ["ANDIE_TRADING_SIGNAL_DEDUPE_SECONDS"] = "3600"
        try:
            with patch("autonomy.trading_agent._publish_event", return_value=True):
                first = await trading_agent.run_agent(context)
                second = await trading_agent.run_agent(context)
        finally:
            os.environ.pop("ANDIE_TRADING_SIGNAL_DEDUPE_SECONDS", None)

        self.assertEqual(first["status"], "approval_required")
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(second["reason"], "duplicate_signal")


if __name__ == "__main__":
    unittest.main()
