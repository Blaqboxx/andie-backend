import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

workspace_root = Path(__file__).resolve().parent.parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from interfaces.api.self_healing.detector import detect_issues
from interfaces.api.self_healing.recovery import recover
from interfaces.api.self_healing.verifier import verify_recovery


class DetectorTests(unittest.TestCase):
    def test_detects_queue_stuck_and_high_cpu(self):
        state = {"queue": 7, "queueDelta": 0, "cpu": 95}

        issues = detect_issues(state, [])

        issue_types = {issue["type"] for issue in issues}
        self.assertEqual(issue_types, {"queue_stuck", "high_cpu"})

    def test_detects_agent_failure_from_workflow_events(self):
        state = {"queue": 0, "queueDelta": 0, "cpu": 10}
        events = [
            {
                "type": "workflow_step_complete",
                "step": "process_agent",
                "workflowId": "wf-1",
                "result": {"status": "failed"},
            }
        ]

        issues = detect_issues(state, events)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "agent_failure")
        self.assertEqual(issues[0]["agent"], "process_agent")


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recover_routes_issue_to_workflow_stream(self):
        with patch(
            "interfaces.api.self_healing.recovery.workflow_engine.run_workflow_stream",
            new=AsyncMock(return_value={"evaluation": {"status": "warning"}}),
        ) as run_workflow_stream:
            result = await recover({"type": "queue_stuck"}, iteration=3, state={"queue": 7})

        self.assertEqual(result["status"], "recovery_started")
        self.assertEqual(result["task"], "run process optimization workflow")
        run_workflow_stream.assert_awaited_once()


class VerifierTests(unittest.TestCase):
    def test_verify_recovery_uses_issue_specific_thresholds(self):
        self.assertTrue(verify_recovery({"queue": 2, "cpu": 50, "failed": 0}, {"type": "queue_stuck"}))
        self.assertFalse(verify_recovery({"queue": 4, "cpu": 50, "failed": 0}, {"type": "queue_stuck"}))
        self.assertTrue(verify_recovery({"queue": 0, "cpu": 70, "failed": 0}, {"type": "high_cpu"}))
        self.assertFalse(verify_recovery({"queue": 0, "cpu": 85, "failed": 0}, {"type": "high_cpu"}))


if __name__ == "__main__":
    unittest.main()