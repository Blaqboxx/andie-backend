import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import autonomy.learning_engine as learning_engine
from autonomy.memory_store import MemoryStore
from interfaces.api.outcome_tracking import record_skill_outcome_internal


class EffectivenessTrendPhase5JTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_memory = learning_engine.memory
        learning_engine.memory = MemoryStore(str(Path(self.temp_dir.name) / "skill_memory.json"))
        self.addCleanup(self._restore_memory)

    def _restore_memory(self):
        learning_engine.memory = self.original_memory

    def test_outcome_ingestion_reports_baseline_and_trend_events(self):
        payload = record_skill_outcome_internal(
            "historical_action",
            result="success",
            record_execution=False,
            intent_type="portfolio_escalation",
            governance_profile="mission_critical",
            effectiveness_score=0.84,
            portfolio_group="media_ops",
        )

        trend = payload.get("effectiveness_trend_update") or {}
        baseline_event = trend.get("baseline_update") or {}
        trend_event = trend.get("trend_update") or {}

        self.assertEqual(baseline_event.get("event"), "coordinator.effectiveness_baseline_updated")
        self.assertEqual(trend_event.get("event"), "coordinator.effectiveness_trend_updated")
        self.assertEqual(baseline_event.get("sample_count"), 1)
        self.assertEqual(trend_event.get("sample_count"), 1)
        self.assertEqual(baseline_event.get("portfolio_group"), "media_ops")

    def test_governance_profiles_remain_isolated_for_effectiveness_rollups(self):
        record_skill_outcome_internal(
            "historical_action",
            result="success",
            record_execution=False,
            intent_type="portfolio_escalation",
            governance_profile="mission_critical",
            effectiveness_score=0.91,
            portfolio_group="media_ops",
        )

        isolated = learning_engine.memory.get_effectiveness_trend(
            intent_type="portfolio_escalation",
            governance_profile="balanced",
            portfolio_group="media_ops",
        )

        self.assertFalse(isolated.get("available"))
        self.assertEqual(isolated["window_30d"]["sample_count"], 0)
        self.assertEqual(isolated["window_90d"]["sample_count"], 0)

    def test_window_rotation_event_emits_when_old_samples_age_out(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        now_ts = datetime.now(timezone.utc).isoformat()

        learning_engine.memory.record_effectiveness_trend(
            intent_type="portfolio_escalation",
            governance_profile="mission_critical",
            effectiveness_score=0.20,
            portfolio_group="media_ops",
            observed_at=old_ts,
        )
        latest = learning_engine.memory.record_effectiveness_trend(
            intent_type="portfolio_escalation",
            governance_profile="mission_critical",
            effectiveness_score=0.90,
            portfolio_group="media_ops",
            observed_at=now_ts,
        )

        rotation = latest.get("window_rotation_update") or {}
        self.assertEqual(rotation.get("event"), "coordinator.effectiveness_window_rotated")
        self.assertGreaterEqual(int(rotation.get("removed_samples", 0) or 0), 1)
        self.assertEqual(latest["registry"]["window_90d"]["sample_count"], 1)

    def test_autonomy_outcome_route_exists_and_emits_trend_events(self):
        source = Path(REPO_ROOT / "interfaces/api/main.py").read_text()
        self.assertIn('@router.post("/autonomy/outcome")', source)
        self.assertIn('"baseline_update", "trend_update", "window_rotation_update"', source)
        self.assertIn('"type": update.get("event")', source)

    def test_autonomy_engine_emits_effectiveness_update_events(self):
        source = Path(REPO_ROOT / "interfaces/api/autonomy_engine.py").read_text()
        self.assertIn('effectiveness_trend_update', source)
        self.assertIn('"baseline_update", "trend_update", "window_rotation_update"', source)
        self.assertIn('"type": update.get("event")', source)

    def test_replay_normalizer_retains_effectiveness_fields(self):
        source = Path(REPO_ROOT / "interfaces/api/main.py").read_text()
        self.assertIn('"type": str(item.get("type") or to_state)', source)
        self.assertIn('"intent_type": item.get("intent_type")', source)
        self.assertIn('"governance_profile": item.get("governance_profile")', source)
        self.assertIn('"portfolio_group": item.get("portfolio_group")', source)


if __name__ == "__main__":
    unittest.main()
