import unittest

from autonomy.governance import evaluate_go_no_go


class GovernanceTests(unittest.TestCase):
    def test_go_when_all_thresholds_met(self):
        result = evaluate_go_no_go(
            {
                "replacement_success_rate": 0.75,
                "sample_size": 25,
                "drift_rate": 0.05,
                "learning_density": 7.0,
            }
        )
        self.assertEqual(result["decision"], "GO")
        self.assertEqual(result["reasons"], [])

    def test_no_go_when_thresholds_fail(self):
        result = evaluate_go_no_go(
            {
                "replacement_success_rate": 0.65,
                "sample_size": 12,
                "drift_rate": 0.2,
                "learning_density": 2.0,
            }
        )
        self.assertEqual(result["decision"], "NO_GO")
        self.assertIn("insufficient_sample_size", result["reasons"])
        self.assertIn("low_replacement_success", result["reasons"])
        self.assertIn("high_drift_rate", result["reasons"])
        self.assertIn("low_signal_density", result["reasons"])

    def test_confidence_tier_synthetic_when_no_real_data(self):
        result = evaluate_go_no_go(
            {
                "replacement_success_rate": 0.75,
                "sample_size": 25,
                "real_sample_size": 0,
                "drift_rate": 0.05,
                "learning_density": 7.0,
            }
        )
        self.assertEqual(result["confidence_tier"], "synthetic")

    def test_confidence_tier_mixed_when_partial_real_data(self):
        result = evaluate_go_no_go(
            {
                "replacement_success_rate": 0.75,
                "sample_size": 30,
                "real_sample_size": 10,
                "drift_rate": 0.05,
                "learning_density": 7.0,
            }
        )
        self.assertEqual(result["confidence_tier"], "mixed")

    def test_confidence_tier_production_when_sufficient_real_data(self):
        result = evaluate_go_no_go(
            {
                "replacement_success_rate": 0.75,
                "sample_size": 50,
                "real_sample_size": 25,
                "drift_rate": 0.05,
                "learning_density": 7.0,
            }
        )
        self.assertEqual(result["confidence_tier"], "production")
        self.assertEqual(result["decision"], "GO")


if __name__ == "__main__":
    unittest.main()
