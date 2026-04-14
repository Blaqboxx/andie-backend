import unittest
from unittest.mock import patch
from pathlib import Path
import sys

workspace_root = Path(__file__).resolve().parent.parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from interfaces.api import autonomy_engine


class AutonomyEngineDecisionTests(unittest.TestCase):
    def setUp(self):
        autonomy_engine.LAST_DECISION = None
        autonomy_engine.LAST_DECISION_TIME = 0.0

    def test_decide_action_sets_process_optimization_once_per_condition(self):
        high_queue = {"cpu": 10, "memory": 40, "queue": 7, "running": 0, "failed": 0}

        with patch("interfaces.api.autonomy_engine.time.time", side_effect=[100.0, 120.0]):
            first = autonomy_engine.decide_action(high_queue)
            second = autonomy_engine.decide_action(high_queue)

        self.assertEqual(first, "run process optimization workflow")
        self.assertIsNone(second)

    def test_decide_action_respects_cooldown(self):
        high_queue = {"cpu": 10, "memory": 40, "queue": 7, "running": 0, "failed": 0}
        recovery = {"cpu": 90, "memory": 40, "queue": 0, "running": 0, "failed": 0}

        with patch("interfaces.api.autonomy_engine.time.time", side_effect=[100.0, 105.0, 116.0]):
            first = autonomy_engine.decide_action(high_queue)
            second = autonomy_engine.decide_action(recovery)
            third = autonomy_engine.decide_action(recovery)

        self.assertEqual(first, "run process optimization workflow")
        self.assertIsNone(second)
        self.assertEqual(third, "run recovery workflow")

    def test_decide_action_resets_process_memory_when_queue_stabilizes(self):
        high_queue = {"cpu": 10, "memory": 40, "queue": 7, "running": 0, "failed": 0}
        stable_queue = {"cpu": 10, "memory": 40, "queue": 1, "running": 0, "failed": 0}

        with patch("interfaces.api.autonomy_engine.time.time", side_effect=[100.0, 120.0, 140.0]):
            first = autonomy_engine.decide_action(high_queue)
            autonomy_engine.decide_action(stable_queue)
            third = autonomy_engine.decide_action(high_queue)

        self.assertEqual(first, "run process optimization workflow")
        self.assertEqual(third, "run process optimization workflow")


if __name__ == "__main__":
    unittest.main()