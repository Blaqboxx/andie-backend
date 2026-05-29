import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autonomy.memory_store import MemoryStore


class EffectivenessRollupsPhase5JTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.memory = MemoryStore(str(Path(self.temp_dir.name) / "skill_memory.json"))

    def _seed(self):
        for score in [0.90, 0.86, 0.84]:
            self.memory.record_effectiveness_trend(
                intent_type="portfolio_escalation",
                governance_profile="mission_critical",
                portfolio_group="media_ops",
                effectiveness_score=score,
            )
        for score in [0.50, 0.46]:
            self.memory.record_effectiveness_trend(
                intent_type="workflow_recovery",
                governance_profile="mission_critical",
                portfolio_group="media_ops",
                effectiveness_score=score,
            )
        for score in [0.20, 0.30, 0.25]:
            self.memory.record_effectiveness_trend(
                intent_type="portfolio_escalation",
                governance_profile="balanced",
                portfolio_group="trading_ops",
                effectiveness_score=score,
            )

    def test_portfolio_rollup_aggregates_across_intents(self):
        self._seed()
        rollup = self.memory.get_effectiveness_portfolio_rollup("media_ops")
        self.assertEqual(rollup["portfolio_group"], "media_ops")
        self.assertGreater(rollup["window_90d"]["sample_count"], 0)
        self.assertIn("portfolio_escalation", rollup["coverage"]["intent_types"])
        self.assertIn("workflow_recovery", rollup["coverage"]["intent_types"])

    def test_governance_rollup_aggregates_across_portfolios(self):
        self._seed()
        rollup = self.memory.get_effectiveness_governance_rollup("mission_critical")
        self.assertEqual(rollup["governance_profile"], "mission_critical")
        self.assertGreater(rollup["window_90d"]["sample_count"], 0)
        self.assertIn("media_ops", rollup["coverage"]["portfolio_groups"])

    def test_comparative_baseline_trend_is_exposed(self):
        self._seed()
        rollup = self.memory.get_effectiveness_portfolio_rollup("trading_ops")
        baseline = rollup.get("comparative_baseline") or {}
        self.assertIn(baseline.get("trend"), {"improving", "stable", "declining"})
        self.assertIsInstance(baseline.get("delta_30d_vs_90d"), float)

    def test_summary_contains_portfolio_and_governance_rollups(self):
        self._seed()
        summary = self.memory.get_effectiveness_summary()
        self.assertGreaterEqual(len(summary.get("portfolio_rollups") or []), 2)
        self.assertGreaterEqual(len(summary.get("governance_rollups") or []), 2)
        self.assertEqual(summary.get("overall", {}).get("scope"), "overall")

    def test_main_contains_layer2_effectiveness_endpoints(self):
        source = Path(REPO_ROOT / "interfaces/api/main.py").read_text()
        self.assertIn('@router.get("/autonomy/effectiveness/portfolio/{portfolio_group}")', source)
        self.assertIn('@router.get("/autonomy/effectiveness/governance/{governance_profile}")', source)
        self.assertIn('@router.get("/autonomy/effectiveness/summary")', source)


if __name__ == "__main__":
    unittest.main()
