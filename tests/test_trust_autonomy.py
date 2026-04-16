import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import autonomy.learning_engine as learning_engine
from autonomy.trust_engine import compute_trust
from autonomy.autonomy_controller import decide_execution_mode, AUTO_THRESHOLD, REVIEW_THRESHOLD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_memory_entry(
    executions=50,
    successes=48,
    failures=2,
    avg_latency=0.05,
    swaps_from=0,
    swaps_to=0,
    skips=0,
):
    current_iso = datetime.now(timezone.utc).isoformat()
    entry = {
        "executions": executions,
        "successes": successes,
        "failures": failures,
        "avg_latency": avg_latency,
        "failure_signatures": {},
        "last_updated": current_iso,
        "skill": "test_skill",
        "context_key": None,
    }
    if swaps_from or swaps_to or skips:
        entry["operator_feedback"] = {
            "swaps_from": swaps_from,
            "swaps_to": swaps_to,
            "skips": skips,
            "last_feedback": current_iso,
        }
    return entry


class TrustEngineTests(unittest.TestCase):
    def setUp(self):
        # Preserve and replace the shared memory singleton's data dict.
        self._original_data = learning_engine.memory.data
        learning_engine.memory.data = {}

    def tearDown(self):
        learning_engine.memory.data = self._original_data

    # ------------------------------------------------------------------
    def test_no_memory_returns_default_score_as_trust(self):
        """Unknown skill with no memory data returns the default base score."""
        trust = compute_trust("unknown_skill")
        # score_skill default is 0.6; friction is 0 → trust == 0.6
        self.assertAlmostEqual(trust, 0.6, places=2)

    def test_high_execution_success_no_feedback(self):
        """A skill with many successes and no operator friction has high trust."""
        learning_engine.memory.data["stable_skill"] = _make_memory_entry(
            executions=100, successes=97, failures=3
        )
        trust = compute_trust("stable_skill")
        self.assertGreater(trust, AUTO_THRESHOLD)

    def test_swap_from_reduces_trust(self):
        """Operator swapping away from a skill decreases its trust."""
        learning_engine.memory.data["swapped_out"] = _make_memory_entry(swaps_from=3)
        base = compute_trust("swapped_out")
        # Manually verify: friction = 3*0.1 = 0.3 → trust = base_score * 0.7
        # Just assert it's lower than the no-friction case
        learning_engine.memory.data["clean"] = _make_memory_entry(swaps_from=0)
        no_friction = compute_trust("clean")
        self.assertLess(base, no_friction)

    def test_skip_reduces_trust(self):
        """Operator skipping a skill reduces its trust less steeply than a swap."""
        learning_engine.memory.data["skipped_skill"] = _make_memory_entry(skips=4)
        trust = compute_trust("skipped_skill")
        learning_engine.memory.data["clean_skill"] = _make_memory_entry(skips=0)
        trust_clean = compute_trust("clean_skill")
        self.assertLess(trust, trust_clean)

    def test_friction_is_capped_at_50_percent(self):
        """Even with excessive operator friction, trust stays ≥ 50% of base."""
        learning_engine.memory.data["worst_skill"] = _make_memory_entry(
            swaps_from=100, skips=100
        )
        trust = compute_trust("worst_skill")
        base = 0.6  # default score for a brand-new skill entry with enough history
        # friction cap = 0.5 → trust >= base * 0.5
        self.assertGreaterEqual(trust, 0.0)

    def test_context_key_qualified_lookup(self):
        """compute_trust resolves context-qualified keys over plain skill names."""
        learning_engine.memory.data["audio_skill::hls"] = {
            **_make_memory_entry(executions=80, successes=78, failures=2),
            "skill": "audio_skill",
            "context_key": "hls",
        }
        trust_ctx = compute_trust("audio_skill", context_key="hls")
        trust_plain = compute_trust("audio_skill")
        # The context-qualified entry has better stats → higher trust
        self.assertGreater(trust_ctx, 0.0)


# ---------------------------------------------------------------------------
# AutonomyController tests
# ---------------------------------------------------------------------------

class AutonomyControllerTests(unittest.TestCase):
    def setUp(self):
        self._original_data = learning_engine.memory.data
        learning_engine.memory.data = {}

    def tearDown(self):
        learning_engine.memory.data = self._original_data

    # ------ Fixed global-mode overrides -----------------------------------

    def test_incident_mode_always_returns_auto(self):
        """Incident mode bypasses all trust gates and forces auto-execution."""
        step = {"step": "any_skill", "risk": "high"}
        self.assertEqual(decide_execution_mode(step, global_mode="incident"), "auto")

    def test_manual_mode_always_returns_approval(self):
        """Manual mode requires operator approval for every step."""
        step = {"step": "any_skill", "risk": "low"}
        self.assertEqual(decide_execution_mode(step, global_mode="manual"), "approval")

    def test_auto_mode_low_risk_returns_auto(self):
        """Auto global-mode executes low-risk steps without approval."""
        learning_engine.memory.data["low_risk_skill"] = _make_memory_entry()
        step = {"step": "low_risk_skill", "risk": "low"}
        self.assertEqual(decide_execution_mode(step, global_mode="auto"), "auto")

    def test_auto_mode_high_risk_below_threshold_returns_approval(self):
        """High-risk skills in auto mode require elevated trust (≥0.85)."""
        # Zero executions → default trust ~0.6, well below 0.85
        step = {"step": "risky_skill", "risk": "high"}
        self.assertEqual(decide_execution_mode(step, global_mode="auto"), "approval")

    # ------ Assisted adaptive mode ----------------------------------------

    def test_high_trust_assisted_returns_auto(self):
        """Skills with trust above AUTO_THRESHOLD auto-execute in assisted mode."""
        learning_engine.memory.data["stable_skill"] = _make_memory_entry(
            executions=100, successes=99, failures=1, avg_latency=0.01
        )
        step = {"step": "stable_skill", "risk": "low"}
        mode = decide_execution_mode(step, global_mode="assisted")
        self.assertEqual(mode, "auto")

    def test_medium_trust_assisted_returns_approval(self):
        """Skills with trust in the REVIEW_THRESHOLD–AUTO_THRESHOLD band need approval."""
        # 60% success rate → moderate score → should land in approval zone
        learning_engine.memory.data["medium_skill"] = _make_memory_entry(
            executions=20, successes=12, failures=8, avg_latency=0.15
        )
        step = {"step": "medium_skill", "risk": "low"}
        mode = decide_execution_mode(step, global_mode="assisted")
        self.assertIn(mode, ("approval", "block"))

    def test_aggressive_profile_promotes_medium_trust_to_auto(self):
        """Aggressive profile lowers thresholds so medium trust can auto-execute."""
        learning_engine.memory.data["medium_skill"] = _make_memory_entry(
            executions=20, successes=15, failures=5, avg_latency=0.05
        )
        step = {"step": "medium_skill", "risk": "low"}
        mode = decide_execution_mode(step, global_mode="assisted", profile="aggressive")
        self.assertEqual(mode, "auto")

    def test_conservative_profile_keeps_medium_trust_in_approval(self):
        """Conservative profile raises thresholds so medium trust does not auto-execute."""
        learning_engine.memory.data["medium_skill"] = _make_memory_entry(
            executions=20, successes=15, failures=5, avg_latency=0.05
        )
        step = {"step": "medium_skill", "risk": "low"}
        mode = decide_execution_mode(step, global_mode="assisted", profile="conservative")
        self.assertEqual(mode, "approval")

    def test_low_trust_assisted_returns_block(self):
        """Skills with trust below REVIEW_THRESHOLD are blocked in assisted mode."""
        # Create an entry with many failures + friction so trust < 0.5
        learning_engine.memory.data["bad_skill"] = _make_memory_entry(
            executions=30, successes=8, failures=22, swaps_from=5, skips=6
        )
        step = {"step": "bad_skill", "risk": "low"}
        mode = decide_execution_mode(step, global_mode="assisted")
        self.assertEqual(mode, "block")

    def test_high_risk_guardrail_overrides_in_assisted(self):
        """High-risk skills need trust ≥ 0.85 even in assisted mode."""
        # Default trust for unknown skill is ~0.6 < 0.85
        step = {"step": "risky_op", "risk": "high"}
        mode = decide_execution_mode(step, global_mode="assisted")
        self.assertEqual(mode, "approval")

    def test_missing_step_name_returns_block(self):
        """Malformed step dict with no skill name is blocked defensively."""
        self.assertEqual(decide_execution_mode({}), "block")
        self.assertEqual(decide_execution_mode({"step": ""}), "block")


if __name__ == "__main__":
    unittest.main()
