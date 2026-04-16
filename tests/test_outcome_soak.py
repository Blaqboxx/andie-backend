import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import autonomy.learning_engine as learning_engine
import autonomy.runtime_config as runtime_config_module
from autonomy.memory_store import MemoryStore
from interfaces.api.outcome_tracking import record_skill_outcome_internal


@unittest.skipUnless(os.environ.get("ANDIE_RUN_SOAK") == "1", "Set ANDIE_RUN_SOAK=1 to run soak test")
class OutcomeSoakTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_learning_memory = learning_engine.memory
        self.original_runtime_config = dict(runtime_config_module.RUNTIME_CONFIG)

        test_memory = MemoryStore(str(Path(self.temp_dir.name) / "skill_memory.json"))
        learning_engine.memory = test_memory
        runtime_config_module.RUNTIME_CONFIG.clear()
        runtime_config_module.RUNTIME_CONFIG.update(self.original_runtime_config)
        self.addCleanup(self._restore)

    def _restore(self):
        learning_engine.memory = self.original_learning_memory
        runtime_config_module.RUNTIME_CONFIG.clear()
        runtime_config_module.RUNTIME_CONFIG.update(self.original_runtime_config)

    def test_concurrent_outcome_writes_and_scoring(self):
        jobs = 400
        workers = 12

        def write_one(i):
            return record_skill_outcome_internal(
                skill_name="resync_audio",
                result="success" if i % 5 else "failure",
                context_key="hls_stream",
                replaced_from="analyze_video",
                latency=0.02,
                record_execution=True,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(write_one, i) for i in range(jobs)]
            for fut in as_completed(futures):
                payload = fut.result()
                self.assertTrue(payload.get("recorded"))

        key = "resync_audio::hls"
        data = learning_engine.memory.data.get(key) or {}
        self.assertGreaterEqual(int(data.get("executions", 0) or 0), jobs)
        outcomes = data.get("replacement_outcomes") or {}
        self.assertGreaterEqual(int(outcomes.get("total", 0) or 0), jobs)
