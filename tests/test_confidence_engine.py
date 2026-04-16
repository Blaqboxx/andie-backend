import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autonomy.confidence_engine import evaluate_overseer_decision


class ConfidenceEngineTests(unittest.TestCase):
    def test_high_quality_signal_approves(self):
        result = evaluate_overseer_decision(
            confidence=0.86,
            risk_score=0.24,
            data_quality=0.9,
            data_coverage=0.82,
            volatility=0.21,
            profile="balanced",
            time_horizon="swing",
            confidence_threshold=0.75,
            max_risk_score=0.45,
            min_data_quality=0.6,
        )

        self.assertEqual(result["decision"], "approve")
        self.assertIn(result["execution"], ["buy_strong", "buy", "accumulate_small"])
        self.assertGreater(result["composite_score"], 0.60)
        self.assertTrue(result["risk_adjusted"])
        self.assertTrue(len(result["reason_trace"]) >= 3)
        self.assertEqual(result["profile"], "balanced")
        self.assertIn("weights", result)
        self.assertIn("strategy_confidence", result["signals"])

    def test_poor_signal_holds(self):
        result = evaluate_overseer_decision(
            confidence=0.48,
            risk_score=0.78,
            data_quality=0.52,
            data_coverage=0.4,
            volatility=0.71,
            profile="aggressive",
            time_horizon="short_term",
            confidence_threshold=0.75,
            max_risk_score=0.45,
            min_data_quality=0.6,
        )

        self.assertEqual(result["decision"], "hold")
        self.assertEqual(result["execution"], "hold")
        self.assertLessEqual(result["composite_score"], 0.60)
        self.assertFalse(result["risk_adjusted"])
        self.assertTrue(result["risk_guardrail_triggered"])

    def test_auto_profile_switches_from_time_horizon(self):
        long_term = evaluate_overseer_decision(
            confidence=0.8,
            risk_score=0.2,
            data_quality=0.85,
            data_coverage=0.7,
            volatility=0.2,
            profile=None,
            time_horizon="long_term",
            confidence_threshold=0.75,
            max_risk_score=0.45,
            min_data_quality=0.6,
        )
        short_term = evaluate_overseer_decision(
            confidence=0.8,
            risk_score=0.2,
            data_quality=0.85,
            data_coverage=0.7,
            volatility=0.2,
            profile=None,
            time_horizon="short_term",
            confidence_threshold=0.75,
            max_risk_score=0.45,
            min_data_quality=0.6,
        )

        self.assertEqual(long_term["profile"], "conservative")
        self.assertEqual(short_term["profile"], "aggressive")


if __name__ == "__main__":
    unittest.main()
