import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interfaces.api.main import app
from interfaces.api.orchestrator_runtime import execute_local_command
import interfaces.api.skill_control as skill_control
import interfaces.api.plan_store as plan_store
import interfaces.api.main as api_main
import autonomy.learning_engine as learning_engine
import autonomy.trust_engine as trust_engine
import autonomy.runtime_config as runtime_config_module
from autonomy.policy_audit import audit_logger
from autonomy.control_plane_metrics import control_plane_metrics
from skills import executor as skill_executor
from autonomy.memory_store import MemoryStore


class SkillsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_settings_path = skill_control.SETTINGS_CONFIG_PATH
        self.original_audit_path = audit_logger.path
        self.original_plans_dir = plan_store.PLANS_DIR
        self.original_runtime_config = dict(runtime_config_module.RUNTIME_CONFIG)
        self.original_api_memory = api_main.skill_learning_memory
        self.original_learning_memory = learning_engine.memory
        self.original_trust_memory = trust_engine.memory
        self.original_executor_memory = skill_executor.memory
        skill_control.SETTINGS_CONFIG_PATH = Path(self.temp_dir.name) / "control_plane_settings.json"
        audit_logger.path = Path(self.temp_dir.name) / "policy_audit.log"
        plan_store.PLANS_DIR = Path(self.temp_dir.name) / "plans"
        test_memory = MemoryStore(str(Path(self.temp_dir.name) / "skill_memory.json"))
        api_main.skill_learning_memory = test_memory
        learning_engine.memory = test_memory
        trust_engine.memory = test_memory
        skill_executor.memory = test_memory
        runtime_config_module.RUNTIME_CONFIG.clear()
        runtime_config_module.RUNTIME_CONFIG.update(self.original_runtime_config)
        for key in list(control_plane_metrics._counters.keys()):
            control_plane_metrics._counters[key] = 0
        self.addCleanup(self._restore_skill_control)
        self.client = TestClient(app)

    def _restore_skill_control(self):
        skill_control.SETTINGS_CONFIG_PATH = self.original_settings_path
        audit_logger.path = self.original_audit_path
        plan_store.PLANS_DIR = self.original_plans_dir
        api_main.skill_learning_memory = self.original_api_memory
        learning_engine.memory = self.original_learning_memory
        trust_engine.memory = self.original_trust_memory
        skill_executor.memory = self.original_executor_memory
        runtime_config_module.RUNTIME_CONFIG.clear()
        runtime_config_module.RUNTIME_CONFIG.update(self.original_runtime_config)

    def test_skills_endpoint_lists_builtin_skills(self):
        response = self.client.get("/skills")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = {entry["name"] for entry in payload["skills"]}
        self.assertIn("resync_audio", names)
        self.assertIn("analyze_video", names)
        self.assertIn("restart_server", names)

    def test_skill_proposal_selects_audio_skill(self):
        response = self.client.post(
            "/skills/propose",
            json={"task": "Please resync audio for stream alpha"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["proposal"]["selectedSkill"], "resync_audio")
        self.assertGreater(payload["proposal"]["confidence"], 0.35)

    def test_skill_tools_endpoint_exports_openai_style_tools(self):
        response = self.client.get("/skills/tools")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = {entry["function"]["name"] for entry in payload["tools"]}
        self.assertIn("resync_audio", names)
        self.assertIn("restart_server", names)

    def test_skill_plan_builds_dependency_order(self):
        response = self.client.post(
            "/skills/plan",
            json={"task": "Please resync audio for stream alpha"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["plan"]["selectedSkill"], "resync_audio")
        self.assertEqual(
            set(payload["plan"]["plan"]),
            {"analyze_video", "resync_audio", "verify_stream_health"},
        )
        self.assertEqual(len(payload["scoredPlan"]), 3)
        self.assertIn("profile", payload)
        self.assertIn(payload["profile"], {"conservative", "balanced", "aggressive"})
        self.assertIn("pruned", payload)
        self.assertIsInstance(payload["pruned"], list)
        self.assertIn("planStability", payload)
        self.assertIn("drift", payload)
        self.assertIn("detected", payload["drift"])
        self.assertIn("intensity", payload["drift"])
        self.assertIn("severity", payload["drift"])
        self.assertAlmostEqual(sum(step["normalized"] for step in payload["scoredPlan"]), 1.0, places=3)
        self.assertIn("risk", payload["scoredPlan"][0])
        self.assertIn("requires_approval", payload["scoredPlan"][0])
        self.assertIn("instability", payload["scoredPlan"][0])
        self.assertIn("failure_signatures", payload["scoredPlan"][0])

    def test_skill_execute_runs_low_risk_skill(self):
        response = self.client.post(
            "/skills/execute",
            json={"skill": "resync_audio", "params": {"stream_id": "alpha"}},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["execution"]["skill"], "resync_audio")

    def test_skill_execute_requires_approval_for_high_risk(self):
        response = self.client.post(
            "/skills/execute",
            json={"skill": "restart_server", "params": {"service": "backend"}},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "pending_approval")
        self.assertTrue(payload["requiresApproval"])

    def test_skill_execute_step_requires_approval_until_approved(self):
        pending = self.client.post(
            "/skills/execute-step",
            json={"step": "restart_server", "params": {"service": "backend"}},
        )
        self.assertEqual(pending.status_code, 200)
        pending_payload = pending.json()
        self.assertEqual(pending_payload["status"], "pending_approval")
        self.assertTrue(pending_payload["stepMeta"]["requires_approval"])

        approved = self.client.post(
            "/skills/execute-step",
            json={"step": "restart_server", "params": {"service": "backend"}, "approved": True},
        )
        self.assertEqual(approved.status_code, 200)
        approved_payload = approved.json()
        self.assertEqual(approved_payload["status"], "ok")
        self.assertEqual(approved_payload["execution"]["skill"], "restart_server")

    def test_skill_execute_step_rejects_with_reason(self):
        response = self.client.post(
            "/skills/execute-step",
            json={"step": "restart_server", "params": {"service": "backend"}, "reason": "operator_rejected"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["reason"], "operator_rejected")

    def test_skill_plan_execute_stops_for_high_risk_plan(self):
        response = self.client.post(
            "/skills/plan/execute",
            json={"task": "Restart the backend service", "params": {"service": "backend"}},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "pending_approval")
        self.assertEqual(payload["execution"]["blockedOn"], "restart_server")

    def test_skill_plan_execute_runs_full_low_risk_plan(self):
        response = self.client.post(
            "/skills/plan/execute",
            json={"task": "Please resync audio for stream alpha", "params": {"stream_id": "alpha", "video_id": "alpha"}},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["scoredPlan"]), 3)
        completed_names = [entry["skill"] for entry in payload["execution"]["completed"]]
        self.assertEqual(
            set(completed_names),
            {"analyze_video", "resync_audio", "verify_stream_health"},
        )
        self.assertIn("drift", payload)
        self.assertIn("replacementOutcomes", payload)
        self.assertIn("success", payload["replacementOutcomes"])
        self.assertIn("failure", payload["replacementOutcomes"])

    def test_skill_learning_endpoint_returns_snapshots(self):
        response = self.client.get("/skills/learning")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("skills", payload)
        self.assertIn("entries", payload)
        self.assertIn("memoryPath", payload)

    def test_skill_outcome_endpoint_records_replacement_stats(self):
        response = self.client.post(
            "/skills/outcome",
            json={
                "skill": "restart_server",
                "replaced_from": "check_service_status",
                "context_key": "hls_stream",
                "result": "success",
                "latency": 0.12,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["recorded"])
        self.assertEqual(payload["snapshot"]["replacement_outcomes"]["success"], 1)
        self.assertEqual(payload["snapshot"]["replacement_pair"]["success"], 1)

        feedback_response = self.client.get("/skills/feedback")
        self.assertEqual(feedback_response.status_code, 200)
        feedback = feedback_response.json()["feedback"]
        self.assertEqual(feedback["restart_server::hls"]["replacement_outcomes"]["success"], 1)

    def test_execute_skill_records_replacement_outcome_when_provided(self):
        response = self.client.post(
            "/skills/execute",
            json={
                "skill": "resync_audio",
                "params": {
                    "stream_id": "alpha",
                    "video_id": "alpha",
                    "context_key": "hls_stream",
                    "replaced_from": "analyze_video",
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["outcome"]["recorded"])
        self.assertEqual(payload["outcome"]["snapshot"]["replacement_outcomes"]["total"], 1)

    def test_execute_step_records_replacement_outcome_from_metadata(self):
        response = self.client.post(
            "/skills/execute-step",
            json={
                "step": "resync_audio",
                "approved": True,
                "params": {
                    "stream_id": "alpha",
                    "video_id": "alpha",
                    "context_key": "hls_stream",
                },
                "metadata": {
                    "replaced_from": "analyze_video",
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["outcome"]["snapshot"]["replacement_pair"]["success"], 1)

    def test_execute_edited_plan_records_replacement_outcomes(self):
        response = self.client.post(
            "/skills/plan/execute-edited",
            json={
                "task": "Resync audio on stream alpha",
                "params": {"stream_id": "alpha", "video_id": "alpha", "context_key": "hls_stream"},
                "edited_plan": [
                    {"step": "resync_audio", "recommended_action": "auto_execute", "replacement_for": "analyze_video"},
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["completed"][0]["outcome"]["snapshot"]["replacement_outcomes"]["success"], 1)

    def test_skill_control_endpoint_updates_blacklist(self):
        response = self.client.put(
            "/skills/control",
            json={"blacklisted_skills": ["resync_audio"], "incident_mode": False, "actor": "test-suite", "reason": "unstable_failure_pattern"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "saved")
        self.assertIn("resync_audio", payload["controlState"]["blacklisted_skills"])

        get_response = self.client.get("/skills/control")
        self.assertEqual(get_response.status_code, 200)
        get_payload = get_response.json()
        self.assertIn("resync_audio", get_payload["controlState"]["blacklisted_skills"])

    def test_blacklisted_skill_is_excluded_from_plan_and_execution(self):
        self.client.put(
            "/skills/control",
            json={"blacklisted_skills": ["resync_audio"], "actor": "test-suite", "reason": "unstable_failure_pattern"},
        )

        plan_response = self.client.post(
            "/skills/plan",
            json={"task": "Please resync audio for stream alpha"},
        )
        self.assertEqual(plan_response.status_code, 200)
        plan_payload = plan_response.json()
        self.assertIsNone(plan_payload["plan"]["selectedSkill"])
        self.assertTrue(any(entry["skill"] == "resync_audio" for entry in plan_payload["suppressedSkills"]))

        execute_response = self.client.post(
            "/skills/execute-step",
            json={"step": "resync_audio", "params": {"stream_id": "alpha"}},
        )
        self.assertEqual(execute_response.status_code, 200)
        execute_payload = execute_response.json()
        self.assertEqual(execute_payload["status"], "blocked")
        self.assertEqual(execute_payload["reason"], "blacklisted")

    def test_incident_mode_suppresses_unstable_skills(self):
        with patch("interfaces.api.skill_control.skill_memory_snapshot", return_value={"unstable": True, "executions": 4, "failures": 3}):
            self.client.put(
                "/skills/control",
                json={"incident_mode": True, "actor": "test-suite", "reason": "critical_outage"},
            )

            response = self.client.post(
                "/skills/execute-step",
                json={"step": "resync_audio", "params": {"stream_id": "alpha"}},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reason"], "incident_mode_unstable")

    def test_skill_control_rejects_sensitive_changes_without_reason(self):
        response = self.client.put(
            "/skills/control",
            json={"blacklisted_skills": ["resync_audio"], "actor": "test-suite"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.put(
            "/skills/control",
            json={"incident_mode": True, "actor": "test-suite"},
        )
        self.assertEqual(response.status_code, 400)

    def test_skill_control_writes_policy_audit_log(self):
        response = self.client.put(
            "/skills/control",
            json={
                "blacklisted_skills": ["resync_audio"],
                "incident_mode": True,
                "actor": "test-suite",
                "reason": "operator_drill",
                "request_id": "req-123",
            },
        )
        self.assertEqual(response.status_code, 200)

        lines = audit_logger.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(lines), 2)
        events = [json.loads(line) for line in lines]
        actions = {event.get("action") for event in events}
        self.assertIn("incident_mode_toggle", actions)
        self.assertIn("blacklist_skill", actions)
        self.assertTrue(all(event.get("reason") for event in events))
        self.assertTrue(all(event.get("request_id") == "req-123" for event in events))

    def test_plan_snapshot_save_list_and_load(self):
        save_response = self.client.post(
            "/skills/plan/save",
            json={
                "name": "audio-recovery",
                "task": "Resync audio on stream alpha",
                "edited_plan": [
                    {"step": "analyze_video", "recommended_action": "auto_execute"},
                    {"step": "resync_audio", "recommended_action": "operator_review", "requires_approval": False},
                ],
                "edit_trail": [
                    {
                        "action": "swap",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "metadata": {
                            "from": "restart_server",
                            "to": "check_service_status",
                            "context_key": "stream::alpha",
                        },
                    }
                ],
                "actor": "test-suite",
                "request_id": "snap-1",
            },
        )
        self.assertEqual(save_response.status_code, 200)
        save_payload = save_response.json()
        self.assertEqual(save_payload["status"], "saved")
        self.assertEqual(save_payload["feedbackRecorded"], 1)
        filename = save_payload["snapshot"]["filename"]

        list_response = self.client.get("/skills/plan/snapshots")
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        self.assertTrue(any(item["filename"] == filename for item in list_payload["snapshots"]))

        load_response = self.client.get(f"/skills/plan/snapshots/{filename}")
        self.assertEqual(load_response.status_code, 200)
        load_payload = load_response.json()
        self.assertEqual(load_payload["snapshot"]["name"], "audio-recovery")
        self.assertEqual(load_payload["snapshot"]["task"], "Resync audio on stream alpha")
        self.assertEqual(len(load_payload["snapshot"]["editablePlan"]), 2)

        latest_response = self.client.get("/skills/plan/snapshots/latest")
        self.assertEqual(latest_response.status_code, 200)
        latest_payload = latest_response.json()
        self.assertEqual(latest_payload["snapshot"]["filename"], filename)
        self.assertEqual(latest_payload["snapshot"]["name"], "audio-recovery")

    def test_skills_override_records_feedback_immediately(self):
        response = self.client.post(
            "/skills/override",
            json={
                "type": "swap",
                "from_skill": "check_service_status",
                "to_skill": "restart_encoder",
                "context_key": "hls_stream",
                "source": "replacement_candidate",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("recorded"))
        self.assertEqual(payload.get("type"), "swap")

        before_from = payload["from"]["previous_score"]
        after_from = payload["from"]["updated_score"]
        before_to = payload["to"]["previous_score"]
        after_to = payload["to"]["updated_score"]

        self.assertGreaterEqual(before_from, after_from)
        self.assertGreaterEqual(after_to, before_to)

        summary = self.client.get("/skills/feedback")
        self.assertEqual(summary.status_code, 200)
        data = summary.json().get("feedback", {})
        self.assertIn("check_service_status::hls", data)
        self.assertIn("restart_encoder::hls", data)

    def test_execute_edited_plan_runs_non_skipped_steps(self):
        response = self.client.post(
            "/skills/plan/execute-edited",
            json={
                "task": "Resync audio on stream alpha",
                "params": {"stream_id": "alpha", "video_id": "alpha"},
                "edited_plan": [
                    {"step": "analyze_video", "recommended_action": "auto_execute"},
                    {"step": "resync_audio", "recommended_action": "operator_review", "skipped": True},
                    {"step": "verify_stream_health", "recommended_action": "operator_review"},
                ],
                "actor": "test-suite",
                "request_id": "exec-1",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "done")
        completed_names = [entry["step"] for entry in payload["completed"]]
        self.assertEqual(completed_names, ["analyze_video", "verify_stream_health"])
        self.assertEqual(payload["skipped"], ["resync_audio"])

    def test_control_plane_metrics_endpoint_reports_rates(self):
        self.client.post(
            "/skills/plan/execute-edited",
            json={
                "task": "Resync audio on stream alpha",
                "edited_plan": [
                    {"step": "analyze_video", "recommended_action": "auto_execute"},
                    {"step": "verify_stream_health", "recommended_action": "operator_review"},
                ],
                "actor": "test-suite",
                "request_id": "metrics-1",
            },
        )

        metrics_response = self.client.get("/metrics/control-plane")
        self.assertEqual(metrics_response.status_code, 200)
        metrics = metrics_response.json()
        self.assertIn("counters", metrics)
        self.assertIn("rates", metrics)
        self.assertGreaterEqual(metrics["counters"].get("plan_execute_total", 0), 1)
        self.assertIsNotNone(metrics["rates"].get("approval_rate"))
        self.assertIsNotNone(metrics["rates"].get("auto_execution_rate"))
        self.assertIn("pruned_step_count", metrics["counters"])
        self.assertIn("simulation_runs", metrics["counters"])
        self.assertIn("pruned_predicted_failures", metrics["counters"])
        self.assertIn("replaced_step_count", metrics["counters"])
        self.assertIn("outcome_events_total", metrics["counters"])
        self.assertIn("simulation_usage_rate", metrics["rates"])
        self.assertIn("prune_effectiveness", metrics["rates"])
        self.assertIn("learning_signal_density", metrics["rates"])

    def test_latest_snapshot_prefers_max_saved_at_timestamp(self):
        now = datetime.now(timezone.utc)
        older = {
            "name": "older",
            "savedAt": (now - timedelta(minutes=10)).isoformat(),
            "savedBy": "test-suite",
            "task": "older task",
            "editablePlan": [{"step": "analyze_video"}],
            "editTrail": [],
            "requestId": "old",
        }
        newer = {
            "name": "newer",
            "savedAt": now.isoformat(),
            "savedBy": "test-suite",
            "task": "newer task",
            "editablePlan": [{"step": "verify_stream_health"}],
            "editTrail": [],
            "requestId": "new",
        }

        plan_store.PLANS_DIR.mkdir(parents=True, exist_ok=True)
        # Intentionally use misleading filenames so ordering cannot depend on lexicographic names.
        (plan_store.PLANS_DIR / "zz_older.json").write_text(json.dumps(older), encoding="utf-8")
        (plan_store.PLANS_DIR / "aa_newer.json").write_text(json.dumps(newer), encoding="utf-8")

        latest_response = self.client.get("/skills/plan/snapshots/latest")
        self.assertEqual(latest_response.status_code, 200)
        latest_payload = latest_response.json()
        self.assertEqual(latest_payload["snapshot"]["name"], "newer")
        self.assertEqual(latest_payload["snapshot"]["task"], "newer task")

    def test_latest_snapshot_returns_404_when_none_exist(self):
        latest_response = self.client.get("/skills/plan/snapshots/latest")
        self.assertEqual(latest_response.status_code, 404)

    def test_runtime_proposes_skill_for_matching_task(self):
        result = execute_local_command(
            "resync audio for stream bravo",
            "",
            {},
            "echo restart",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["mode"], "skill_proposal")
        self.assertEqual(result["result"]["proposal"]["selectedSkill"], "resync_audio")
        self.assertEqual(result["result"]["proposal"]["plan"], ["analyze_video", "resync_audio", "verify_stream_health"])

    def test_runtime_executes_skill_when_requested(self):
        result = execute_local_command(
            "resync audio for stream bravo",
            "",
            {"executeSkill": True, "skillParams": {"stream_id": "bravo", "video_id": "bravo"}},
            "echo restart",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["mode"], "skill_execution")
        completed_names = [entry["skill"] for entry in result["result"]["execution"]["completed"]]
        self.assertEqual(completed_names, ["analyze_video", "resync_audio", "verify_stream_health"])

    def test_runtime_skill_execution_emits_replacement_outcomes(self):
        result = execute_local_command(
            "resync audio for stream bravo",
            "",
            {
                "executeSkill": True,
                "skillParams": {
                    "stream_id": "bravo",
                    "video_id": "bravo",
                    "context_key": "hls_stream",
                    "replacement_map": {
                        "resync_audio": "analyze_video",
                    },
                },
            },
            "echo restart",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["mode"], "skill_execution")
        self.assertIn("replacementOutcomes", result["result"]["execution"])
        self.assertGreaterEqual(result["result"]["execution"]["replacementOutcomes"]["success"], 1)

        completed = result["result"]["execution"]["completed"]
        tracked = [entry for entry in completed if entry.get("skill") == "resync_audio"]
        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["outcome"]["snapshot"]["replacement_pair"]["success"], 1)

    def test_runtime_outcome_emission_flag_disables_outcome_payload(self):
        runtime_config_module.update_runtime_config({"runtime_outcome_emission_enabled": False})

        result = execute_local_command(
            "resync audio for stream bravo",
            "",
            {
                "executeSkill": True,
                "skillParams": {
                    "stream_id": "bravo",
                    "video_id": "bravo",
                    "context_key": "hls_stream",
                    "replacement_map": {
                        "resync_audio": "analyze_video",
                    },
                },
            },
            "echo restart",
        )
        self.assertEqual(result["status"], "completed")
        execution = result["result"]["execution"]
        self.assertTrue(execution.get("replacementOutcomes", {}).get("disabled"))
        self.assertTrue(all("outcome" not in entry for entry in execution.get("completed", [])))

    def test_outcome_ingestion_failure_emits_alert_counter(self):
        with patch("interfaces.api.outcome_tracking.learning_engine.memory.log_execution", side_effect=RuntimeError("io_failure")):
            response = self.client.post(
                "/skills/outcome",
                json={
                    "skill": "restart_server",
                    "context_key": "hls_stream",
                    "result": "success",
                    "latency": 0.1,
                },
            )

        self.assertEqual(response.status_code, 500)
        counters = control_plane_metrics.snapshot()
        self.assertGreaterEqual(counters.get("alert_outcome_ingestion_failures", 0), 1)

    def test_failure_cascade_breaker_aborts_plan(self):
        plan = ["analyze_video", "resync_audio", "verify_stream_health", "analyze_video"]

        with patch("skills.executor.execute_skill", side_effect=Exception("timeout")):
            result = skill_executor.execute_skill_plan(plan, {"stream_id": "x"})

        self.assertEqual(result["status"], "aborted_failure_cascade")
        self.assertEqual(result["failureCount"], 3)

    def test_autonomy_config_get_and_update(self):
        get_response = self.client.get("/autonomy/config")
        self.assertEqual(get_response.status_code, 200)
        self.assertIn("config", get_response.json())

        update_response = self.client.post(
            "/autonomy/config",
            json={"profile": "aggressive", "exploration_rate": 0.2, "trust_smoothing": 0.9},
        )
        self.assertEqual(update_response.status_code, 200)
        payload = update_response.json()
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(payload["config"]["profile"], "aggressive")
        self.assertAlmostEqual(payload["config"]["exploration_rate"], 0.2, places=3)
        self.assertAlmostEqual(payload["config"]["trust_smoothing"], 0.9, places=3)

    def test_autonomy_profile_endpoint_sets_profile(self):
        response = self.client.post("/autonomy/profile", params={"profile": "conservative"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(payload["profile"], "conservative")

        bad = self.client.post("/autonomy/profile", params={"profile": "unknown"})
        self.assertEqual(bad.status_code, 400)

    def test_autonomy_drift_and_safe_mode_reset_endpoints(self):
        update = self.client.post(
            "/autonomy/config",
            json={"forced_mode": "manual", "drift_detected": True, "drift_reason": "test_drift"},
        )
        self.assertEqual(update.status_code, 200)

        drift = self.client.get("/autonomy/drift")
        self.assertEqual(drift.status_code, 200)
        drift_payload = drift.json()
        self.assertTrue(drift_payload["drift_detected"])
        self.assertEqual(drift_payload["forced_mode"], "manual")
        self.assertEqual(drift_payload["drift_reason"], "test_drift")
        self.assertIn("drift_intensity", drift_payload)
        self.assertIn("drift_severity", drift_payload)
        self.assertIn("metrics", drift_payload)

        reset = self.client.post("/autonomy/safe-mode/reset")
        self.assertEqual(reset.status_code, 200)
        reset_payload = reset.json()
        self.assertEqual(reset_payload["status"], "updated")
        self.assertFalse(reset_payload["drift_detected"])
        self.assertEqual(reset_payload["drift_intensity"], 0.0)
        self.assertEqual(reset_payload["drift_severity"], "stable")

        drift_after = self.client.get("/autonomy/drift")
        self.assertEqual(drift_after.status_code, 200)
        after_payload = drift_after.json()
        self.assertFalse(after_payload["drift_detected"])
        self.assertIsNone(after_payload["forced_mode"])
        self.assertIsNone(after_payload["drift_reason"])
        self.assertEqual(after_payload["drift_intensity"], 0.0)
        self.assertEqual(after_payload["drift_severity"], "stable")

    def test_plan_optimize_preserves_non_blocked_steps_even_when_below_threshold(self):
        response = self.client.post(
            "/skills/plan/optimize",
            json={
                "plan": [
                    {"step": "unknown_skill"},
                    {"step": "required_unknown", "required": True},
                ],
                "min_trust_threshold": 0.7,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        output_steps = payload["plan"]
        self.assertEqual(payload["inputSteps"], 2)
        self.assertEqual(payload["outputSteps"], 2)
        self.assertEqual(len(payload["kept"]), 2)
        self.assertEqual(len(payload["avoided"]), 0)
        self.assertEqual(len(payload["replaced"]), 0)
        self.assertEqual(len(payload["pruned"]), 0)
        self.assertIn("drift", payload)
        self.assertIn("detected", payload["drift"])
        self.assertIn("severity", payload["drift"])
        self.assertEqual({item["step"] for item in output_steps}, {"unknown_skill", "required_unknown"})

    def test_plan_optimize_prunes_when_blocked(self):
        with patch("autonomy.plan_optimizer.decide_execution_mode", return_value="block"):
            response = self.client.post(
                "/skills/plan/optimize",
                json={
                    "plan": [
                        {"step": "unknown_skill"},
                        {"step": "required_unknown", "required": True},
                    ],
                    "min_trust_threshold": 0.7,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["inputSteps"], 2)
        self.assertEqual(payload["outputSteps"], 1)
        self.assertEqual(len(payload["avoided"]), 1)
        self.assertEqual(len(payload["replaced"]), 0)
        self.assertEqual(len(payload["pruned"]), 1)
        self.assertEqual(payload["pruned"][0]["step"], "unknown_skill")
        self.assertEqual(payload["pruned"][0]["reason"], "predicted_failure")
        self.assertIn("failure_probability", payload["pruned"][0])

    def test_plan_optimize_replaces_blocked_step_with_alternative(self):
        def _mode(step, **_kwargs):
            return "block" if step.get("step") == "restart_server" else "approval"

        def _trust(skill_name, *_args, **_kwargs):
            return 0.2 if skill_name == "restart_server" else 0.9

        with patch("autonomy.plan_optimizer.decide_execution_mode", side_effect=_mode), patch("autonomy.plan_optimizer.compute_trust", side_effect=_trust):
            response = self.client.post(
                "/skills/plan/optimize",
                json={
                    "plan": [{"step": "restart_server"}],
                    "min_trust_threshold": 0.95,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["inputSteps"], 1)
        self.assertEqual(len(payload["avoided"]), 1)
        self.assertEqual(len(payload["replaced"]), 1)
        self.assertEqual(len(payload["pruned"]), 0)
        self.assertEqual(payload["avoided"][0]["step"], "restart_server")
        self.assertNotEqual(payload["replaced"][0]["replacement"]["skill"], "restart_server")

    def test_plan_optimize_drift_detection_forces_manual_mode(self):
        def _mode(step, **_kwargs):
            return "block" if step.get("step") == "restart_server" else "approval"

        def _trust(skill_name, *_args, **_kwargs):
            return 0.2 if skill_name == "restart_server" else 0.95

        with patch("autonomy.plan_optimizer.decide_execution_mode", side_effect=_mode), patch(
            "autonomy.plan_optimizer.compute_trust", side_effect=_trust
        ), patch(
            "interfaces.api.main.control_plane_metrics.snapshot",
            return_value={"plan_execute_total": 10.0, "plan_execute_failed": 3.0},
        ):
            response = self.client.post(
                "/skills/plan/optimize",
                json={"plan": [{"step": "restart_server"}]},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["replaced"]), 1)
        self.assertEqual(len(payload["pruned"]), 0)
        self.assertIn("drift", payload)
        self.assertTrue(payload["drift"]["detected"])
        self.assertEqual(payload["drift"]["forcedMode"], "manual")
        self.assertGreater(payload["drift"]["intensity"], 0.5)
        self.assertIn(payload["drift"]["severity"], {"moderate", "severe"})
        self.assertIsNotNone(payload["drift"]["reason"])

        drift_state = self.client.get("/autonomy/drift")
        self.assertEqual(drift_state.status_code, 200)
        drift_payload = drift_state.json()
        self.assertTrue(drift_payload["drift_detected"])
        self.assertEqual(drift_payload["forced_mode"], "manual")
        self.assertGreater(drift_payload["drift_intensity"], 0.5)
        self.assertIn(drift_payload["drift_severity"], {"moderate", "severe"})

    def test_plan_optimize_drift_recovery_clears_manual_mode(self):
        seed = self.client.post(
            "/autonomy/config",
            json={"forced_mode": "manual", "drift_detected": True, "drift_reason": "drift_detected prior"},
        )
        self.assertEqual(seed.status_code, 200)

        with patch("autonomy.plan_optimizer.compute_trust", return_value=0.95), patch(
            "autonomy.plan_optimizer.decide_execution_mode", return_value="approval"
        ), patch(
            "interfaces.api.main.control_plane_metrics.snapshot",
            return_value={"plan_execute_total": 10.0, "plan_execute_failed": 0.0},
        ):
            response = self.client.post(
                "/skills/plan/optimize",
                json={"plan": [{"step": "verify_stream_health"}]},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("drift", payload)
        self.assertFalse(payload["drift"]["detected"])
        self.assertTrue(payload["drift"]["recovered"])
        self.assertEqual(payload["drift"]["severity"], "stable")
        self.assertIsNone(payload["drift"]["forcedMode"])
        self.assertIsNone(payload["drift"]["reason"])

        drift_state = self.client.get("/autonomy/drift")
        self.assertEqual(drift_state.status_code, 200)
        drift_payload = drift_state.json()
        self.assertFalse(drift_payload["drift_detected"])
        self.assertIsNone(drift_payload["forced_mode"])
        self.assertIsNone(drift_payload["drift_reason"])
        self.assertEqual(drift_payload["drift_severity"], "stable")

    def test_plan_optimize_uses_profile_dynamic_threshold_when_not_provided(self):
        with patch("autonomy.plan_optimizer.decide_execution_mode", return_value="block"):
            conservative = self.client.post(
                "/skills/plan/optimize",
                json={
                    "plan": [{"step": "unknown_skill"}],
                    "profile": "conservative",
                },
            )
            aggressive = self.client.post(
                "/skills/plan/optimize",
                json={
                    "plan": [{"step": "unknown_skill"}],
                    "profile": "aggressive",
                },
            )

        self.assertEqual(conservative.status_code, 200)
        self.assertEqual(aggressive.status_code, 200)
        self.assertEqual(conservative.json()["minTrustThreshold"], 0.7)
        self.assertEqual(aggressive.json()["minTrustThreshold"], 0.3)

    def test_simulation_endpoint_does_not_apply_feedback_by_default(self):
        before = self.client.get("/skills/feedback")
        self.assertEqual(before.status_code, 200)
        before_total = before.json().get("total_skills", 0)

        sim = self.client.post(
            "/skills/simulate",
            json={
                "plan": [{"step": "analyze_video"}, {"step": "resync_audio"}],
                "failure_rate": 1.0,
                "seed": 7,
                "apply_feedback": False,
            },
        )
        self.assertEqual(sim.status_code, 200)
        sim_payload = sim.json()
        self.assertEqual(sim_payload["status"], "ok")
        self.assertEqual(sim_payload["simulation"]["feedbackApplied"], 0)
        self.assertTrue(sim_payload.get("isolated"))
        self.assertTrue(sim_payload["simulation"].get("predictive"))
        self.assertIn("failure_probability", sim_payload["simulation"]["results"][0])

        after = self.client.get("/skills/feedback")
        self.assertEqual(after.status_code, 200)
        after_total = after.json().get("total_skills", 0)
        self.assertEqual(after_total, before_total)

    def test_simulation_endpoint_isolates_feedback_even_when_enabled(self):
        before = self.client.get("/skills/feedback")
        self.assertEqual(before.status_code, 200)
        before_total = before.json().get("total_skills", 0)

        sim = self.client.post(
            "/skills/simulate",
            json={
                "plan": [{"step": "analyze_video"}, {"step": "resync_audio"}],
                "failure_rate": 1.0,
                "seed": 42,
                "apply_feedback": True,
            },
        )
        self.assertEqual(sim.status_code, 200)
        sim_payload = sim.json()
        self.assertEqual(sim_payload["status"], "ok")
        self.assertTrue(sim_payload.get("isolated"))
        failed_count = sum(1 for item in sim_payload["simulation"]["results"] if item.get("status") == "failed")
        self.assertEqual(sim_payload["simulation"]["feedbackApplied"], failed_count)

        after = self.client.get("/skills/feedback")
        self.assertEqual(after.status_code, 200)
        after_total = after.json().get("total_skills", 0)
        self.assertEqual(after_total, before_total)

    def test_simulation_endpoint_supports_non_predictive_mode(self):
        sim = self.client.post(
            "/skills/simulate",
            json={
                "plan": [{"step": "analyze_video"}],
                "failure_rate": 0.0,
                "seed": 7,
                "apply_feedback": False,
                "predictive": False,
            },
        )
        self.assertEqual(sim.status_code, 200)
        payload = sim.json()
        self.assertFalse(payload["simulation"]["predictive"])


if __name__ == "__main__":
    unittest.main()
