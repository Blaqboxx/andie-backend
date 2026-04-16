import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import autonomy.learning_engine as learning_engine
import autonomy.runtime_config as runtime_config_module
import skills.executor as skills_executor
from autonomy.confidence_engine import evaluate_plan
from autonomy.control_plane_metrics import control_plane_metrics
from autonomy.learning_engine import build_context_key
from autonomy.memory_store import MemoryStore


class LearningEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.memory_path = os.path.join(self.temp_dir.name, "skill_memory.json")

        self.original_learning_memory = learning_engine.memory
        self.original_executor_memory = skills_executor.memory
        self.original_runtime_config = dict(runtime_config_module.RUNTIME_CONFIG)

        test_memory = MemoryStore(self.memory_path)
        learning_engine.memory = test_memory
        skills_executor.memory = test_memory
        runtime_config_module.RUNTIME_CONFIG.clear()
        runtime_config_module.RUNTIME_CONFIG.update(self.original_runtime_config)
        for key in list(control_plane_metrics._counters.keys()):
            control_plane_metrics._counters[key] = 0

        self.addCleanup(self._restore_memory)

    def _restore_memory(self):
        learning_engine.memory = self.original_learning_memory
        skills_executor.memory = self.original_executor_memory
        runtime_config_module.RUNTIME_CONFIG.clear()
        runtime_config_module.RUNTIME_CONFIG.update(self.original_runtime_config)

    def test_score_skill_defaults_without_history(self):
        self.assertEqual(learning_engine.score_skill("unknown_skill"), 0.6)

    def test_score_improves_with_successful_executions(self):
        for _ in range(10):
            learning_engine.memory.log_execution("resync_audio", success=True, latency=0.1)
        score = learning_engine.score_skill("resync_audio")
        self.assertGreater(score, 0.6)

    def test_context_keyed_learning_scores_independently(self):
        for _ in range(10):
            learning_engine.memory.log_execution("resync_audio", success=True, latency=0.1, context_key="hls")
        for _ in range(10):
            learning_engine.memory.log_execution("resync_audio", success=False, latency=0.5, error="timeout", context_key="rtmp")

        hls_score = learning_engine.score_skill("resync_audio", context_key="hls")
        rtmp_score = learning_engine.score_skill("resync_audio", context_key="rtmp")
        self.assertGreater(hls_score, rtmp_score)

    def test_context_key_builder_canonicalizes_fields(self):
        key = build_context_key(
            {
                "stream_type": "HLS_Stream",
                "protocol": "RTMP",
                "encoder_type": "FFmpeg",
                "region": "US-West",
            }
        )
        self.assertEqual(key, "hls_stream::rtmp::ffmpeg::us-west")

    def test_learning_score_is_clamped(self):
        for _ in range(20):
            learning_engine.memory.log_execution("super_fast_skill", success=True, latency=0.0)
        score = learning_engine.score_skill("super_fast_skill")
        self.assertLessEqual(score, 0.95)
        self.assertGreaterEqual(score, 0.05)

    def test_evaluate_plan_includes_learned_scores(self):
        for _ in range(10):
            learning_engine.memory.log_execution("analyze_video", success=True, latency=0.1)
        scored = evaluate_plan(["analyze_video", "resync_audio"], context_key="hls")
        self.assertEqual(scored[0]["step"], "analyze_video")
        self.assertIn("learned_score", scored[0])
        self.assertIn("confidence", scored[0])
        self.assertIn("normalized", scored[0])
        self.assertAlmostEqual(sum(item["normalized"] for item in scored), 1.0, places=3)

    def test_memory_store_uses_context_key_suffix(self):
        learning_engine.memory.log_execution("resync_audio", success=True, latency=0.1, context_key="HLS")
        self.assertIn("resync_audio::hls", learning_engine.memory.data)

    def test_successful_replacement_outcomes_boost_score(self):
        for _ in range(6):
            learning_engine.memory.log_execution("restart_encoder", success=True, latency=0.1, context_key="hls")

        before = learning_engine.score_skill("restart_encoder", context_key="hls")
        for _ in range(5):
            learning_engine.memory.log_replacement_outcome(
                "restart_encoder",
                result="success",
                replaced_from="check_service_status",
                context_key="hls",
            )

        after = learning_engine.score_skill("restart_encoder", context_key="hls")
        self.assertGreater(after, before)

    def test_failed_replacement_outcomes_penalize_score(self):
        for _ in range(6):
            learning_engine.memory.log_execution("restart_encoder", success=True, latency=0.1, context_key="hls")

        before = learning_engine.score_skill("restart_encoder", context_key="hls")
        for _ in range(5):
            learning_engine.memory.log_replacement_outcome(
                "restart_encoder",
                result="failure",
                replaced_from="check_service_status",
                context_key="hls",
            )

        after = learning_engine.score_skill("restart_encoder", context_key="hls")
        self.assertLess(after, before)

    def test_pair_specific_learning_prefers_successful_original(self):
        for _ in range(8):
            learning_engine.memory.log_execution("restart_encoder", success=True, latency=0.1, context_key="hls")

        for _ in range(4):
            learning_engine.memory.log_replacement_outcome(
                "restart_encoder",
                result="success",
                replaced_from="check_service_status",
                context_key="hls",
            )
            learning_engine.memory.log_replacement_outcome(
                "restart_encoder",
                result="failure",
                replaced_from="restart_server",
                context_key="hls",
            )

        preferred = learning_engine.score_skill("restart_encoder", context_key="hls", replaced_from="check_service_status")
        discouraged = learning_engine.score_skill("restart_encoder", context_key="hls", replaced_from="restart_server")
        self.assertGreater(preferred, discouraged)

    def test_low_sample_outcomes_do_not_bias_score(self):
        for _ in range(6):
            learning_engine.memory.log_execution("skill_x", success=True, latency=0.1, context_key="hls")

        before = learning_engine.score_skill("skill_x", context_key="hls")
        learning_engine.memory.log_replacement_outcome("skill_x", result="success", replaced_from="fallback_a", context_key="hls")
        after = learning_engine.score_skill("skill_x", context_key="hls")
        self.assertAlmostEqual(after, before, places=3)

    def test_old_replacement_outcomes_fade_toward_neutral(self):
        for _ in range(8):
            learning_engine.memory.log_execution("restart_encoder", success=True, latency=0.1, context_key="hls")

        baseline = learning_engine.score_skill("restart_encoder", context_key="hls")
        for _ in range(6):
            learning_engine.memory.log_replacement_outcome(
                "restart_encoder",
                result="success",
                replaced_from="check_service_status",
                context_key="hls",
            )

        recent = learning_engine.score_skill("restart_encoder", context_key="hls")
        self.assertGreater(recent, baseline)

        entry = learning_engine.memory.data["restart_encoder::hls"]
        old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        entry["replacement_outcomes"]["last_updated"] = old_ts
        entry["replacement_pairs"]["check_service_status"]["last_updated"] = old_ts

        faded = learning_engine.score_skill("restart_encoder", context_key="hls")
        self.assertLess(faded, recent)

    def test_outcome_weighting_flag_disables_replacement_boost(self):
        for _ in range(8):
            learning_engine.memory.log_execution("restart_encoder", success=True, latency=0.1, context_key="hls")
        for _ in range(5):
            learning_engine.memory.log_replacement_outcome(
                "restart_encoder",
                result="success",
                replaced_from="check_service_status",
                context_key="hls",
            )

        runtime_config_module.update_runtime_config({"outcome_weighting_enabled": True})
        with_weighting = learning_engine.score_skill("restart_encoder", context_key="hls", replaced_from="check_service_status")

        runtime_config_module.update_runtime_config({"outcome_weighting_enabled": False})
        without_weighting = learning_engine.score_skill("restart_encoder", context_key="hls", replaced_from="check_service_status")

        self.assertGreater(with_weighting, without_weighting)

    def test_memory_write_error_emits_alert_counter(self):
        store = MemoryStore(self.memory_path)
        store.data["resync_audio"] = {"executions": 1}

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.save()

        self.assertGreaterEqual(control_plane_metrics.snapshot().get("alert_memory_write_errors", 0), 1)
