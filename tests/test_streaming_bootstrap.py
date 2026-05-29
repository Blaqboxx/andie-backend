from fastapi.testclient import TestClient

from main import (
    AGENT_TASKS_BY_WORKSPACE,
    AGENT_WORKFLOWS_BY_WORKSPACE,
    GOVERNANCE_STATES,
    GOVERNANCE_PROFILE_BINDINGS,
    GOVERNANCE_PROFILE_STATE,
    GOVERNANCE_STATE,
    OBJECTIVES,
    OBJECTIVE_SIGNALS,
    SUPERVISOR_INTENTS_BY_WORKSPACE,
    TRUST_STATE,
    TRUST_STATES,
    app,
)


REQUIRED_KEYS = {
    "event_id",
    "event_type",
    "timestamp",
    "source",
    "payload",
    "version",
    "workspace_id",
}


def _assert_envelope(event: dict, expected_type: str | None = None) -> None:
    for key in REQUIRED_KEYS:
        assert key in event
    if expected_type is not None:
        assert event["event_type"] == expected_type
    assert isinstance(event["payload"], dict)
    assert event["version"] == 1
    assert isinstance(event["event_id"], str) and len(event["event_id"]) > 0


def test_connection_ready_then_workspace_snapshot_sequence() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/stream") as ws:
        first = ws.receive_json()
        second = ws.receive_json()

    _assert_envelope(first, "connection.ready")
    _assert_envelope(second, "workspace.snapshot")
    assert second["sequence"] > first["sequence"]
    assert "snapshot" in second["payload"]


def test_alias_route_normalization_matches_canonical_bootstrap() -> None:
    client = TestClient(app)

    with client.websocket_connect("/ws/stream") as ws:
        canonical = [ws.receive_json(), ws.receive_json()]

    with client.websocket_connect("/ws/backlog") as ws:
        alias = [ws.receive_json(), ws.receive_json()]

    _assert_envelope(canonical[0], "connection.ready")
    _assert_envelope(canonical[1], "workspace.snapshot")
    _assert_envelope(alias[0], "connection.ready")
    _assert_envelope(alias[1], "workspace.snapshot")


def test_replay_drilldown_returns_execution_events() -> None:
    client = TestClient(app)

    payload = {
        "type": "timeline.transition",
        "payload": {"from": "stable", "to": "warming"},
        "execution_id": "exec-123",
    }
    post = client.post("/api/events", json=payload)
    assert post.status_code == 200

    replay = client.get("/api/replay/exec-123")
    assert replay.status_code == 200
    body = replay.json()

    assert body["execution_id"] == "exec-123"
    assert len(body["events"]) >= 1
    _assert_envelope(body["events"][-1], "timeline.transition")
    assert body["events"][-1]["payload"]["to"] == "warming"


def test_workspace_snapshot_api_returns_envelope() -> None:
    client = TestClient(app)
    res = client.get("/api/workspace/snapshot")
    assert res.status_code == 200
    _assert_envelope(res.json(), "workspace.snapshot")


def test_stream_publish_emits_envelope_event() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/stream") as ws:
        _ = ws.receive_json()
        _ = ws.receive_json()

        publish = {
            "action": "publish",
            "type": "telemetry.update",
            "payload": {"value": 42},
            "execution_id": "exec-stream-1",
            "source": "telemetry",
        }
        ws.send_json(publish)
        evt = ws.receive_json()

    _assert_envelope(evt, "telemetry.update")
    assert evt["execution_id"] == "exec-stream-1"
    assert evt["payload"]["value"] == 42


def test_websocket_clean_close() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/events") as ws:
        _ = ws.receive_json()
        _ = ws.receive_json()
        ws.close()


def test_objective_graph_influence_emits_pressure_and_critical_path() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    client = TestClient(app)
    execution_id = "exec-graph-1"

    seed = [
        {
            "objective_id": "gpu-upgrade",
            "title": "GPU Upgrade",
            "priority": 5,
            "salience": 5.0,
            "enables": ["local-training"],
            "execution_id": execution_id,
        },
        {
            "objective_id": "local-training",
            "title": "Local Training",
            "priority": 4,
            "salience": 4.0,
            "blocked_by": ["gpu-upgrade"],
            "enables": ["agent-expansion"],
            "execution_id": execution_id,
        },
        {
            "objective_id": "agent-expansion",
            "title": "Agent Expansion",
            "priority": 3,
            "salience": 3.0,
            "depends_on": ["local-training"],
            "execution_id": execution_id,
        },
    ]

    for payload in seed:
        res = client.post("/api/objectives", json=payload)
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    graph = client.get("/api/objectives/graph")
    assert graph.status_code == 200
    signals = graph.json()["signals"]

    assert signals["blocked"]["local-training"] is True
    assert signals["blocked"]["agent-expansion"] is True
    assert signals["pressure"]["gpu-upgrade"] > signals["pressure"]["agent-expansion"]
    assert 0.0 <= signals["objective_pressure_score"]["gpu-upgrade"] <= 1.0
    assert signals["critical_path"]["gpu-upgrade"] >= 2

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    event_types = [event["event_type"] for event in replay.json()["events"]]
    assert "objective.pressure" in event_types
    assert "objective.critical_path" in event_types


def test_objective_unblocked_signal_emitted_after_dependency_completion() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    client = TestClient(app)
    execution_id = "exec-graph-2"

    parent = {
        "objective_id": "gpu-upgrade",
        "title": "GPU Upgrade",
        "priority": 5,
        "salience": 5.0,
        "execution_id": execution_id,
    }
    child = {
        "objective_id": "local-training",
        "title": "Local Training",
        "priority": 4,
        "salience": 4.0,
        "blocked_by": ["gpu-upgrade"],
        "execution_id": execution_id,
    }

    assert client.post("/api/objectives", json=parent).status_code == 200
    assert client.post("/api/objectives", json=child).status_code == 200

    status = client.post(
        "/api/objectives/gpu-upgrade/status",
        json={"status": "completed", "execution_id": execution_id},
    )
    assert status.status_code == 200
    assert status.json()["signals"]["blocked"]["local-training"] is False

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    event_types = [event["event_type"] for event in replay.json()["events"]]
    assert "objective.completed" in event_types
    assert "objective.unblocked" in event_types


def test_trust_memory_objective_context_recomputes_governance_state() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}
    TRUST_STATE["score"] = 0.5
    GOVERNANCE_STATE["band"] = "stable"

    client = TestClient(app)
    execution_id = "exec-governance-coupling-1"
    workspace_id = "ws-governance-coupling-1"

    assert (
        client.post(
            "/api/objectives",
            json={
                "objective_id": "gpu-upgrade",
                "title": "GPU Upgrade",
                "priority": 5,
                "salience": 5.0,
                "execution_id": execution_id,
                "workspace_id": workspace_id,
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/objectives",
            json={
                "objective_id": "local-training",
                "title": "Local Training",
                "priority": 4,
                "salience": 4.0,
                "blocked_by": ["gpu-upgrade"],
                "execution_id": execution_id,
                "workspace_id": workspace_id,
            },
        ).status_code
        == 200
    )

    trust_res = client.post(
        "/api/trust/recompute",
        json={
            "trust_score": 0.95,
            "reason": "trusted-operator",
            "execution_id": execution_id,
            "workspace_id": workspace_id,
        },
    )
    assert trust_res.status_code == 200
    assert trust_res.json()["trust"]["score"] == 0.95

    for _ in range(3):
        fail = client.post(
            "/api/events",
            json={
                "type": "execution.failed",
                "payload": {"reason": "transient"},
                "execution_id": execution_id,
                "workspace_id": workspace_id,
            },
        )
        assert fail.status_code == 200

    gov = client.post(
        "/api/governance/recompute",
        json={"execution_id": execution_id, "workspace_id": workspace_id},
    )
    assert gov.status_code == 200
    body = gov.json()
    state = body["governance"]

    assert state["inputs"]["failure_pattern_score"] >= 0.6
    assert state["inputs"]["objective_context"]["blocked_count"] >= 1
    assert state["inputs"]["objective_context"]["max_pressure_score"] > 0
    assert state["interrupt_sensitivity"] < 0.5
    assert state["escalation_readiness"] >= 0.5

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    event_types = [event["event_type"] for event in replay.json()["events"]]
    assert "trust.recomputed" in event_types
    assert "governance.stability" in event_types


def test_trust_changed_emits_when_score_delta_is_material() -> None:
    client = TestClient(app)
    execution_id = "exec-trust-delta-1"
    workspace_id = "ws-trust-delta-1"

    a = client.post(
        "/api/trust/recompute",
        json={"trust_score": 0.2, "execution_id": execution_id, "workspace_id": workspace_id},
    )
    assert a.status_code == 200

    b = client.post(
        "/api/trust/recompute",
        json={"trust_score": 0.9, "execution_id": execution_id, "workspace_id": workspace_id},
    )
    assert b.status_code == 200

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    event_types = [event["event_type"] for event in replay.json()["events"]]
    assert "trust.changed" in event_types


def test_governance_profile_apply_emits_profile_event_and_recompute() -> None:
    client = TestClient(app)
    execution_id = "exec-profile-1"

    res = client.post(
        "/api/governance/profile/apply",
        json={"profile": "conservative", "execution_id": execution_id},
    )
    assert res.status_code == 200
    body = res.json()

    assert body["profile"]["active"] == "conservative"
    assert body["governance"]["profile"] == "conservative"

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "governance.profile_applied" in emitted_types
    assert "governance.stability" in emitted_types

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    event_types = [event["event_type"] for event in replay.json()["events"]]
    assert "governance.profile_applied" in event_types


def test_governance_profile_overlay_changes_runtime_behavior() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}
    TRUST_STATE["score"] = 0.5

    client = TestClient(app)
    execution_id = "exec-profile-2"

    assert (
        client.post(
            "/api/objectives",
            json={
                "objective_id": "gpu-upgrade",
                "title": "GPU Upgrade",
                "priority": 5,
                "salience": 5.0,
                "execution_id": execution_id,
            },
        ).status_code
        == 200
    )

    aggressive = client.post(
        "/api/governance/profile/apply",
        json={"profile": "aggressive", "execution_id": execution_id},
    )
    assert aggressive.status_code == 200
    aggressive_cooldown = aggressive.json()["governance"]["cooldown_aggressiveness"]

    conservative = client.post(
        "/api/governance/profile/apply",
        json={"profile": "conservative", "execution_id": execution_id},
    )
    assert conservative.status_code == 200
    conservative_cooldown = conservative.json()["governance"]["cooldown_aggressiveness"]

    assert aggressive_cooldown > conservative_cooldown
    assert GOVERNANCE_PROFILE_STATE["active"] == "conservative"


def test_governance_profile_apply_rejects_unknown_profile() -> None:
    client = TestClient(app)
    res = client.post(
        "/api/governance/profile/apply",
        json={"profile": "does-not-exist", "execution_id": "exec-profile-unknown"},
    )
    assert res.status_code == 400


def test_governance_profile_apply_event_includes_provenance_fields() -> None:
    client = TestClient(app)
    execution_id = "exec-profile-prov-1"
    workspace_id = "ws-provenance-a"

    res = client.post(
        "/api/governance/profile/apply",
        json={
            "profile": "mission_critical",
            "actor": "chief-architect",
            "reason": "incident response posture",
            "execution_id": execution_id,
            "workspace_id": workspace_id,
            "correlation_id": "corr-profile-123",
        },
    )
    assert res.status_code == 200

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200

    profile_events = [e for e in replay.json()["events"] if e["event_type"] == "governance.profile_applied"]
    assert len(profile_events) >= 1
    profile_event = profile_events[-1]

    assert profile_event["workspace_id"] == workspace_id
    assert profile_event["correlation_id"] == "corr-profile-123"
    assert profile_event["payload"]["profile"] == "mission_critical"
    assert profile_event["payload"]["workspace_id"] == workspace_id
    assert profile_event["payload"]["actor"] == "chief-architect"
    assert profile_event["payload"]["reason"] == "incident response posture"
    assert profile_event["payload"]["correlation_id"] == "corr-profile-123"


def test_workspace_scoped_profile_binding_is_isolated() -> None:
    TRUST_STATES.pop("ws-alpha", None)
    TRUST_STATES.pop("ws-beta", None)
    GOVERNANCE_PROFILE_BINDINGS.pop("ws-alpha", None)
    GOVERNANCE_PROFILE_BINDINGS.pop("ws-beta", None)

    client = TestClient(app)

    a = client.post(
        "/api/governance/profile/apply",
        json={"profile": "aggressive", "workspace_id": "ws-alpha", "execution_id": "exec-ws-1"},
    )
    assert a.status_code == 200

    b = client.post(
        "/api/governance/profile/apply",
        json={"profile": "conservative", "workspace_id": "ws-beta", "execution_id": "exec-ws-2"},
    )
    assert b.status_code == 200

    state_a = client.get("/api/governance/state", params={"workspace_id": "ws-alpha"})
    state_b = client.get("/api/governance/state", params={"workspace_id": "ws-beta"})
    assert state_a.status_code == 200
    assert state_b.status_code == 200

    assert state_a.json()["profile"]["active"] == "aggressive"
    assert state_b.json()["profile"]["active"] == "conservative"


def test_agent_role_contracts_assign_and_replay_event() -> None:
    workspace_id = "ws-agent-roles"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    roles = client.get("/api/agents/roles")
    assert roles.status_code == 200
    assert {"planner", "execution", "memory", "governance"}.issubset(set(roles.json()["roles"]))

    execution_id = "exec-agent-assign-1"
    assign = client.post(
        "/api/agents/assign",
        json={
            "task_id": "task-001",
            "role": "planner",
            "objective_id": "obj-001",
            "payload": {"intent": "build plan"},
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert assign.status_code == 200
    body = assign.json()
    assert body["task"]["status"] == "assigned"
    assert body["event"]["event_type"] == "agent.assigned"

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    event_types = [event["event_type"] for event in replay.json()["events"]]
    assert "agent.assigned" in event_types


def test_agent_status_lifecycle_events_emitted() -> None:
    workspace_id = "ws-agent-lifecycle"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-agent-life-1"

    create = client.post(
        "/api/agents/assign",
        json={
            "task_id": "task-002",
            "role": "execution",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert create.status_code == 200

    blocked = client.post(
        "/api/agents/task-002/status",
        json={
            "status": "blocked",
            "reason": "dependency missing",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["event"]["event_type"] == "agent.blocked"

    completed = client.post(
        "/api/agents/task-002/status",
        json={
            "status": "completed",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert completed.status_code == 200
    assert completed.json()["event"]["event_type"] == "agent.completed"

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    event_types = [event["event_type"] for event in replay.json()["events"]]
    assert "agent.assigned" in event_types
    assert "agent.blocked" in event_types
    assert "agent.completed" in event_types


def test_agent_workspace_task_isolation() -> None:
    AGENT_TASKS_BY_WORKSPACE.pop("ws-agent-a", None)
    AGENT_TASKS_BY_WORKSPACE.pop("ws-agent-b", None)

    client = TestClient(app)
    a = client.post(
        "/api/agents/assign",
        json={
            "task_id": "shared-task-id",
            "role": "memory",
            "workspace_id": "ws-agent-a",
            "execution_id": "exec-agent-ws-a",
        },
    )
    assert a.status_code == 200

    b = client.post(
        "/api/agents/assign",
        json={
            "task_id": "shared-task-id",
            "role": "governance",
            "workspace_id": "ws-agent-b",
            "execution_id": "exec-agent-ws-b",
        },
    )
    assert b.status_code == 200

    list_a = client.get("/api/agents/tasks", params={"workspace_id": "ws-agent-a"})
    list_b = client.get("/api/agents/tasks", params={"workspace_id": "ws-agent-b"})
    assert list_a.status_code == 200
    assert list_b.status_code == 200

    assert len(list_a.json()["tasks"]) == 1
    assert len(list_b.json()["tasks"]) == 1
    assert list_a.json()["tasks"][0]["role"] == "memory"
    assert list_b.json()["tasks"][0]["role"] == "governance"


def test_agent_arbitration_emits_assignment_strategy_event() -> None:
    workspace_id = "ws-arb-1"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-arb-1"

    create_obj = client.post(
        "/api/objectives",
        json={
            "objective_id": "obj-arb-1",
            "title": "Priority Objective",
            "priority": 10,
            "salience": 10.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert create_obj.status_code == 200

    arb = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "task-arb-1",
            "objective_id": "obj-arb-1",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert arb.status_code == 200
    body = arb.json()
    assert body["strategy"] in {"pressure_based", "governance_directed", "trust_based", "operator_forced"}
    assert body["emitted_events"][0]["event_type"] == "agent.decision_context"
    assert body["emitted_events"][1]["event_type"] == "agent.assignment_strategy"
    assert body["emitted_events"][2]["event_type"] == "agent.collaboration_plan"
    assert body["emitted_events"][3]["event_type"] == "agent.workflow_started"
    assert body["emitted_events"][4]["event_type"] == "agent.workflow_health"
    assert body["emitted_events"][5]["event_type"] == "agent.assigned"
    assert "workflow" in body["collaboration_plan"]
    assert "workflow_pressure_score" in body["workflow"]

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    event_types = [event["event_type"] for event in replay.json()["events"]]
    assert "agent.decision_context" in event_types
    assert "agent.assignment_strategy" in event_types
    assert "agent.collaboration_plan" in event_types
    assert "agent.workflow_started" in event_types
    assert "agent.workflow_health" in event_types
    assert "agent.assigned" in event_types


def test_agent_arbitration_operator_forced_strategy() -> None:
    workspace_id = "ws-arb-2"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-arb-2"

    arb = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "task-arb-2",
            "operator_forced_role": "memory",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "manual override",
        },
    )
    assert arb.status_code == 200
    body = arb.json()
    assert body["strategy"] == "operator_forced"
    assert body["role"] == "memory"


def test_agent_arbitration_aggressive_high_pressure_prefers_execution() -> None:
    workspace_id = "ws-arb-3"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-arb-3"

    profile = client.post(
        "/api/governance/profile/apply",
        json={
            "profile": "aggressive",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "fast-response mode",
        },
    )
    assert profile.status_code == 200

    create_obj = client.post(
        "/api/objectives",
        json={
            "objective_id": "obj-arb-3",
            "title": "Hot Objective",
            "priority": 10,
            "salience": 10.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert create_obj.status_code == 200

    arb = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "task-arb-3",
            "objective_id": "obj-arb-3",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert arb.status_code == 200
    body = arb.json()
    assert body["strategy"] == "pressure_based"
    assert body["role"] == "execution"
    assert body["collaboration_plan"]["workflow"][:2] == ["execution", "planner"]
    assert body["collaboration_plan"]["reason"] == "aggressive_high_pressure_fast_path"


def test_agent_arbitration_escalated_governance_selects_governance_role() -> None:
    workspace_id = "ws-arb-4"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-arb-4"

    parent = client.post(
        "/api/objectives",
        json={
            "objective_id": "obj-arb-4-parent",
            "title": "Parent Objective",
            "priority": 8,
            "salience": 8.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert parent.status_code == 200

    child = client.post(
        "/api/objectives",
        json={
            "objective_id": "obj-arb-4-child",
            "title": "Child Objective",
            "priority": 8,
            "salience": 8.0,
            "blocked_by": ["obj-arb-4-parent"],
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert child.status_code == 200

    for _ in range(5):
        fail = client.post(
            "/api/events",
            json={
                "type": "execution.failed",
                "payload": {"reason": "stress"},
                "workspace_id": workspace_id,
                "execution_id": execution_id,
            },
        )
        assert fail.status_code == 200

    gov = client.post(
        "/api/governance/recompute",
        json={"workspace_id": workspace_id, "execution_id": execution_id},
    )
    assert gov.status_code == 200
    assert gov.json()["governance"]["band"] == "escalated"

    arb = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "task-arb-4",
            "objective_id": "obj-arb-4-child",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert arb.status_code == 200
    body = arb.json()
    assert body["strategy"] == "governance_directed"
    assert body["role"] == "governance"
    assert body["collaboration_plan"]["workflow"] == ["governance", "planner", "execution"]
    assert body["collaboration_plan"]["reason"] == "escalated_governance_mandatory"


def test_workflow_update_blocked_replans_and_increases_pressure() -> None:
    workspace_id = "ws-workflow-1"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-workflow-1"

    seed = client.post(
        "/api/objectives",
        json={
            "objective_id": "obj-workflow-1",
            "title": "Workflow Objective",
            "priority": 9,
            "salience": 9.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert seed.status_code == 200

    arb = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "wf-task-1",
            "objective_id": "obj-workflow-1",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert arb.status_code == 200
    before_pressure = float(arb.json()["workflow"]["workflow_pressure_score"])

    update = client.post(
        "/api/agents/workflows/wf-task-1/update",
        json={
            "status": "blocked",
            "reason": "dependency timeout",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert update.status_code == 200
    body = update.json()
    after_pressure = float(body["workflow"]["workflow_pressure_score"])
    assert after_pressure >= before_pressure

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "agent.workflow_updated" in emitted_types
    assert "agent.workflow_health" in emitted_types
    assert "agent.workflow_blocked" in emitted_types
    assert "agent.workflow_replanned" in emitted_types

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    replay_types = [event["event_type"] for event in replay.json()["events"]]
    assert "agent.workflow_started" in replay_types
    assert "agent.workflow_blocked" in replay_types
    assert "agent.workflow_replanned" in replay_types


def test_workflow_update_completed_emits_completion_event() -> None:
    workspace_id = "ws-workflow-2"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-workflow-2"

    arb = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "wf-task-2",
            "operator_forced_role": "planner",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert arb.status_code == 200

    done = client.post(
        "/api/agents/workflows/wf-task-2/update",
        json={
            "status": "completed",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert done.status_code == 200
    body = done.json()
    assert body["workflow"]["status"] == "completed"

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "agent.workflow_updated" in emitted_types
    assert "agent.workflow_health" in emitted_types
    assert "agent.workflow_completed" in emitted_types


def test_workflow_delegation_emits_delegated_and_health_events() -> None:
    workspace_id = "ws-workflow-3"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-workflow-3"

    arb = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "wf-task-3",
            "operator_forced_role": "planner",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert arb.status_code == 200

    delegated = client.post(
        "/api/agents/workflows/wf-task-3/delegate",
        json={
            "from_role": "planner",
            "to_role": "memory",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "need context recovery",
        },
    )
    assert delegated.status_code == 200
    emitted_types = [event["event_type"] for event in delegated.json()["emitted_events"]]
    assert "agent.delegated" in emitted_types
    assert "agent.workflow_health" in emitted_types


def test_workflow_review_chain_events_and_consensus_events() -> None:
    workspace_id = "ws-workflow-4"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-workflow-4"

    arb = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "wf-task-4",
            "operator_forced_role": "execution",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert arb.status_code == 200

    review_req = client.post(
        "/api/agents/workflows/wf-task-4/review",
        json={
            "reviewer_role": "governance",
            "status": "requested",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert review_req.status_code == 200
    assert review_req.json()["event"]["event_type"] == "agent.review_requested"

    review_done = client.post(
        "/api/agents/workflows/wf-task-4/review",
        json={
            "reviewer_role": "governance",
            "status": "completed",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert review_done.status_code == 200
    assert review_done.json()["event"]["event_type"] == "agent.review_completed"

    consensus = client.post(
        "/api/agents/workflows/wf-task-4/consensus",
        json={
            "participants": ["planner", "memory", "governance"],
            "reached": True,
            "resolution": "proceed-with-guardrails",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert consensus.status_code == 200
    emitted_types = [event["event_type"] for event in consensus.json()["emitted_events"]]
    assert "agent.consensus_started" in emitted_types
    assert "agent.consensus_reached" in emitted_types
    assert "agent.workflow_health" in emitted_types


def test_workflow_consensus_failed_triggers_supervisor_events() -> None:
    workspace_id = "ws-workflow-5"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-workflow-5"

    arb = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "wf-task-5",
            "operator_forced_role": "planner",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert arb.status_code == 200

    failed = client.post(
        "/api/agents/workflows/wf-task-5/consensus",
        json={
            "participants": ["planner", "governance"],
            "reached": False,
            "resolution": "conflict",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert failed.status_code == 200
    body = failed.json()
    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "agent.consensus_failed" in emitted_types
    assert "agent.supervisor_invoked" in emitted_types
    assert ("agent.supervisor_replanned" in emitted_types) or ("agent.supervisor_redelegated" in emitted_types)
    assert "agent.workflow_health" in emitted_types


def test_workflow_manual_supervision_endpoint_emits_supervisor_events() -> None:
    workspace_id = "ws-workflow-6"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-workflow-6"

    arb = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "wf-task-6",
            "operator_forced_role": "execution",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert arb.status_code == 200

    supervised = client.post(
        "/api/agents/workflows/wf-task-6/supervise",
        json={
            "trigger": "manual",
            "reason": "operator intervention",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert supervised.status_code == 200
    emitted_types = [event["event_type"] for event in supervised.json()["emitted_events"]]
    assert "agent.supervisor_invoked" in emitted_types
    assert (
        ("agent.supervisor_replanned" in emitted_types)
        or ("agent.supervisor_redelegated" in emitted_types)
        or ("agent.supervisor_resumed" in emitted_types)
    )
    assert "agent.workflow_health" in emitted_types
    assert "agent.supervisor_prioritized" in emitted_types


def test_supervisor_cross_workflow_arbitration_emits_priority_and_transfer_events() -> None:
    workspace_id = "ws-supervisor-arb-1"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-supervisor-arb-1"

    a = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "wf-super-a",
            "operator_forced_role": "execution",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert a.status_code == 200

    first_arb = client.post(
        "/api/agents/supervisor/arbitrate",
        json={
            "workspace_id": workspace_id,
            "available_slots": 1,
            "execution_id": execution_id,
            "reason": "initial slot assignment",
        },
    )
    assert first_arb.status_code == 200

    b = client.post(
        "/api/agents/arbitrate",
        json={
            "task_id": "wf-super-b",
            "operator_forced_role": "execution",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert b.status_code == 200

    boost = client.post(
        "/api/agents/workflows/wf-super-b/update",
        json={
            "status": "blocked",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "force pressure increase",
        },
    )
    assert boost.status_code == 200

    second_arb = client.post(
        "/api/agents/supervisor/arbitrate",
        json={
            "workspace_id": workspace_id,
            "available_slots": 1,
            "execution_id": execution_id,
            "reason": "rebalance after pressure change",
        },
    )
    assert second_arb.status_code == 200
    emitted_types = [event["event_type"] for event in second_arb.json()["emitted_events"]]
    assert "agent.supervisor_prioritized" in emitted_types

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    replay_types = [event["event_type"] for event in replay.json()["events"]]
    assert "agent.supervisor_preempted" in replay_types
    assert "agent.supervisor_reallocated" in replay_types
    assert "agent.supervisor_transferred" in replay_types


def test_supervisor_fairness_aging_prevents_starvation() -> None:
    workspace_id = "ws-supervisor-fairness-1"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-supervisor-fairness-1"

    policy = client.post(
        "/api/agents/scheduler/policy",
        json={
            "scheduler_profile": "fair",
            "fairness_window": 2,
            "starvation_threshold": 2,
            "preemption_policy": "allowed",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "enable anti-starvation policy for fairness regression",
        },
    )
    assert policy.status_code == 200

    for workflow_id in ("wf-fair-a", "wf-fair-b", "wf-fair-c"):
        response = client.post(
            "/api/agents/arbitrate",
            json={
                "task_id": workflow_id,
                "operator_forced_role": "execution",
                "workspace_id": workspace_id,
                "execution_id": execution_id,
            },
        )
        assert response.status_code == 200

    active_history: list[str] = []

    base = client.post(
        "/api/agents/supervisor/arbitrate",
        json={
            "workspace_id": workspace_id,
            "available_slots": 1,
            "execution_id": execution_id,
            "reason": "initial arbitration",
        },
    )
    assert base.status_code == 200
    first_active = base.json()["runtime"]["active_workflows"]
    assert len(first_active) == 1
    active_history.append(first_active[0])

    for _ in range(2):
        rerun = client.post(
            "/api/agents/supervisor/arbitrate",
            json={
                "workspace_id": workspace_id,
                "available_slots": 1,
                "execution_id": execution_id,
                "reason": "aging sweep",
            },
        )
        assert rerun.status_code == 200
        active_history.append(rerun.json()["runtime"]["active_workflows"][0])

    final = client.post(
        "/api/agents/supervisor/arbitrate",
        json={
            "workspace_id": workspace_id,
            "available_slots": 1,
            "execution_id": execution_id,
            "reason": "fairness enforcement",
        },
    )
    assert final.status_code == 200

    emitted_types = [event["event_type"] for event in final.json()["emitted_events"]]
    assert "agent.supervisor_prioritized" in emitted_types
    assert "agent.supervisor_aged" in emitted_types
    assert "agent.supervisor_boosted" in emitted_types
    assert "agent.supervisor_starvation_detected" in emitted_types
    assert "agent.supervisor_fairness_applied" in emitted_types

    active_history.append(final.json()["runtime"]["active_workflows"][0])
    assert {"wf-fair-a", "wf-fair-b", "wf-fair-c"}.issubset(set(active_history))


def test_scheduler_policy_apply_emits_policy_event_and_can_be_retrieved() -> None:
    workspace_id = "ws-scheduler-policy-1"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-scheduler-policy-1"

    applied = client.post(
        "/api/agents/scheduler/policy",
        json={
            "scheduler_profile": "mission_critical",
            "fairness_curve": "exponential",
            "starvation_recovery": "aggressive",
            "preemption_policy": "aggressive",
            "fairness_window": 4,
            "starvation_threshold": 2,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "tune mission critical scheduling",
        },
    )
    assert applied.status_code == 200

    body = applied.json()
    _assert_envelope(body["event"], "agent.scheduler_policy_applied")
    assert body["event"]["payload"]["scheduler_profile"] == "mission_critical"
    assert body["event"]["payload"]["fairness_curve"] == "exponential"
    assert body["event"]["payload"]["preemption_policy"] == "aggressive"

    retrieved = client.get(
        "/api/agents/scheduler/policy",
        params={"workspace_id": workspace_id},
    )
    assert retrieved.status_code == 200
    assert retrieved.json()["policy"]["scheduler_profile"] == "mission_critical"
    assert retrieved.json()["policy"]["fairness_window"] == 4
    assert retrieved.json()["policy"]["starvation_threshold"] == 2


def test_scheduler_policy_never_preempts_active_workflows() -> None:
    workspace_id = "ws-scheduler-policy-2"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-scheduler-policy-2"

    applied = client.post(
        "/api/agents/scheduler/policy",
        json={
            "scheduler_profile": "throughput",
            "preemption_policy": "never",
            "fairness_window": 6,
            "starvation_threshold": 6,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "prefer throughput stability",
        },
    )
    assert applied.status_code == 200

    for workflow_id in ("wf-policy-a", "wf-policy-b"):
        response = client.post(
            "/api/agents/arbitrate",
            json={
                "task_id": workflow_id,
                "operator_forced_role": "execution",
                "workspace_id": workspace_id,
                "execution_id": execution_id,
            },
        )
        assert response.status_code == 200

    first = client.post(
        "/api/agents/supervisor/arbitrate",
        json={
            "workspace_id": workspace_id,
            "available_slots": 1,
            "execution_id": execution_id,
            "reason": "initial throughput slot",
        },
    )
    assert first.status_code == 200
    first_active = first.json()["runtime"]["active_workflows"][0]
    hot_workflow = "wf-policy-b" if first_active == "wf-policy-a" else "wf-policy-a"

    updated = client.post(
        f"/api/agents/workflows/{hot_workflow}/update",
        json={
            "status": "blocked",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "raise competing pressure",
        },
    )
    assert updated.status_code == 200

    second = client.post(
        "/api/agents/supervisor/arbitrate",
        json={
            "workspace_id": workspace_id,
            "available_slots": 1,
            "execution_id": execution_id,
            "reason": "throughput stability check",
        },
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["runtime"]["active_workflows"][0] == first_active

    emitted_types = [event["event_type"] for event in second_body["emitted_events"]]
    assert "agent.scheduler_policy_applied" in emitted_types
    assert "agent.supervisor_preempted" not in emitted_types
    assert "agent.supervisor_reallocated" not in emitted_types
    assert "agent.supervisor_transferred" not in emitted_types


def test_scheduler_policy_adapts_under_starvation_pressure() -> None:
    workspace_id = "ws-scheduler-policy-3"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-scheduler-policy-3"

    applied = client.post(
        "/api/agents/scheduler/policy",
        json={
            "scheduler_profile": "balanced",
            "adaptive_mode": True,
            "fairness_window": 2,
            "starvation_threshold": 2,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "enable adaptive scheduler for contention test",
        },
    )
    assert applied.status_code == 200

    for workflow_id in ("wf-adapt-a", "wf-adapt-b", "wf-adapt-c"):
        response = client.post(
            "/api/agents/arbitrate",
            json={
                "task_id": workflow_id,
                "operator_forced_role": "execution",
                "workspace_id": workspace_id,
                "execution_id": execution_id,
            },
        )
        assert response.status_code == 200

    runs = []
    for _ in range(3):
        arbitration = client.post(
            "/api/agents/supervisor/arbitrate",
            json={
                "workspace_id": workspace_id,
                "available_slots": 1,
                "execution_id": execution_id,
                "reason": "adaptive policy sweep",
            },
        )
        assert arbitration.status_code == 200
        runs.append(arbitration.json())

    emitted_types = [event["event_type"] for run in runs for event in run["emitted_events"]]
    assert "agent.scheduler_policy_recommended" in emitted_types
    assert "agent.scheduler_policy_changed" in emitted_types
    assert "agent.scheduler_policy_escalated" in emitted_types

    policy = client.get(
        "/api/agents/scheduler/policy",
        params={"workspace_id": workspace_id},
    )
    assert policy.status_code == 200
    assert policy.json()["policy"]["scheduler_profile"] in {"fair", "mission_critical"}


def test_scheduler_optimization_emits_confidence_effectiveness_and_decay() -> None:
    workspace_id = "ws-scheduler-optimization-1"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-scheduler-optimization-1"

    applied = client.post(
        "/api/agents/scheduler/policy",
        json={
            "scheduler_profile": "balanced",
            "adaptive_mode": True,
            "fairness_window": 2,
            "starvation_threshold": 2,
            "optimization_decay_cycles": 2,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "enable optimization telemetry",
        },
    )
    assert applied.status_code == 200

    for workflow_id in ("wf-opt-a", "wf-opt-b", "wf-opt-c"):
        response = client.post(
            "/api/agents/arbitrate",
            json={
                "task_id": workflow_id,
                "operator_forced_role": "execution",
                "workspace_id": workspace_id,
                "execution_id": execution_id,
            },
        )
        assert response.status_code == 200

    for _ in range(3):
        arbitration = client.post(
            "/api/agents/supervisor/arbitrate",
            json={
                "workspace_id": workspace_id,
                "available_slots": 1,
                "execution_id": execution_id,
                "reason": "optimization escalation sweep",
            },
        )
        assert arbitration.status_code == 200

    for workflow_id in ("wf-opt-b", "wf-opt-c"):
        completed = client.post(
            f"/api/agents/workflows/{workflow_id}/update",
            json={
                "status": "completed",
                "workspace_id": workspace_id,
                "execution_id": execution_id,
                "reason": "reduce contention for decay",
            },
        )
        assert completed.status_code == 200

    for _ in range(3):
        arbitration = client.post(
            "/api/agents/supervisor/arbitrate",
            json={
                "workspace_id": workspace_id,
                "available_slots": 1,
                "execution_id": execution_id,
                "reason": "optimization decay sweep",
            },
        )
        assert arbitration.status_code == 200

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    replay_types = [event["event_type"] for event in replay.json()["events"]]
    assert "agent.scheduler_confidence" in replay_types
    assert "agent.scheduler_effectiveness_scored" in replay_types
    assert "agent.scheduler_contention_smoothed" in replay_types
    assert "agent.scheduler_decay_applied" in replay_types

    policy = client.get(
        "/api/agents/scheduler/policy",
        params={"workspace_id": workspace_id},
    )
    assert policy.status_code == 200
    optimization = policy.json()["policy"]["optimization"]
    assert optimization["decay_cycles"] == 2
    assert 0.0 <= float(optimization["confidence"]) <= 1.0
    assert 0.0 <= float(optimization["effectiveness_score"]) <= 1.0
    assert isinstance(optimization["optimization_history"], list)
    assert len(optimization["optimization_history"]) <= 25


def test_coordinator_priority_ranking_orders_by_objective_pressure() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-1"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-1"

    for objective_id, priority in (("obj-a", 9), ("obj-b", 6), ("obj-c", 3)):
        res = client.post(
            "/api/objectives",
            json={
                "objective_id": objective_id,
                "title": objective_id,
                "priority": priority,
                "salience": float(priority),
                "workspace_id": workspace_id,
                "execution_id": execution_id,
            },
        )
        assert res.status_code == 200

    for objective_id in ("obj-a", "obj-b", "obj-c"):
        arb = client.post(
            "/api/agents/arbitrate",
            json={
                "task_id": f"wf-{objective_id}",
                "objective_id": objective_id,
                "operator_forced_role": "execution",
                "workspace_id": workspace_id,
                "execution_id": execution_id,
            },
        )
        assert arb.status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "rank objective portfolio",
        },
    )
    assert analyze.status_code == 200

    ranked = [item["objective_id"] for item in analyze.json()["priority_ranking"]]
    assert ranked[:3] == ["obj-a", "obj-b", "obj-c"]


def test_coordinator_dependency_block_detects_and_recommends_escalation() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-2"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-2"

    seed = [
        {
            "objective_id": "obj-c",
            "title": "Root blocker",
            "priority": 7,
            "salience": 7.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "obj-b",
            "title": "Blocked objective",
            "priority": 6,
            "salience": 6.0,
            "depends_on": ["obj-c"],
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "obj-a",
            "title": "Dependent objective",
            "priority": 9,
            "salience": 9.0,
            "depends_on": ["obj-b"],
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]
    for payload in seed:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "dependency escalation check",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    blocked_ids = {item["objective_id"] for item in body["blocked_objectives"]}
    assert "obj-b" in blocked_ids

    actions = body["recommended_actions"]
    escalation_actions = [a for a in actions if a.get("type") == "escalation_recommended"]
    assert len(escalation_actions) >= 1

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.blocked_objective_detected" in emitted_types
    assert "coordinator.escalation_recommended" in emitted_types


def test_coordinator_detects_merge_candidates_for_shared_cluster() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-3"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-3"

    obj = client.post(
        "/api/objectives",
        json={
            "objective_id": "obj-shared",
            "title": "Shared cluster",
            "priority": 8,
            "salience": 8.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert obj.status_code == 200

    for task_id in ("wf-x", "wf-y"):
        arb = client.post(
            "/api/agents/arbitrate",
            json={
                "task_id": task_id,
                "objective_id": "obj-shared",
                "operator_forced_role": "execution",
                "workspace_id": workspace_id,
                "execution_id": execution_id,
            },
        )
        assert arb.status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "merge candidate detection",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    assert len(body["merge_candidates"]) >= 1
    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.merge_candidate_detected" in emitted_types


def test_coordinator_governance_aware_recommendations_in_escalated_band() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-4"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-4"

    seed = [
        {
            "objective_id": "obj-z",
            "title": "dependency source",
            "priority": 7,
            "salience": 7.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "obj-y",
            "title": "blocked target",
            "priority": 9,
            "salience": 9.0,
            "depends_on": ["obj-z"],
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]
    for payload in seed:
        assert client.post("/api/objectives", json=payload).status_code == 200

    GOVERNANCE_STATES[workspace_id] = {
        "updated_at": "2026-01-01T00:00:00+00:00",
        "band": "escalated",
        "interrupt_sensitivity": 0.8,
        "escalation_readiness": 0.9,
        "cooldown_aggressiveness": 0.3,
        "posture_persistence": 0.8,
        "governance_attention": 0.9,
        "confidence": 0.7,
        "profile": "mission_critical",
    }

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "governance-aware recommendation",
        },
    )
    assert analyze.status_code == 200
    actions = analyze.json()["recommended_actions"]

    governance_reviews = [a for a in actions if a.get("action") == "governance_review"]
    assert len(governance_reviews) >= 1
    assert not any(a.get("action") == "accelerate" for a in actions)


def test_coordinator_portfolio_created_and_ranked() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-5"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-5"

    seed = [
        {
            "objective_id": "p1-a",
            "title": "program one anchor",
            "priority": 9,
            "salience": 9.0,
            "enables": ["p1-b"],
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "p1-b",
            "title": "program one dependency",
            "priority": 8,
            "salience": 8.0,
            "depends_on": ["p1-a"],
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "p2-a",
            "title": "program two",
            "priority": 3,
            "salience": 3.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]

    for payload in seed:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "portfolio ranking pass",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    assert len(body["objective_portfolios"]) >= 2
    assert len(body["portfolio_ranking"]) >= 2
    top_portfolio_id = body["portfolio_ranking"][0]["portfolio_id"]
    top_portfolio = [p for p in body["objective_portfolios"] if p["portfolio_id"] == top_portfolio_id][0]
    assert set(top_portfolio["objective_ids"]) >= {"p1-a", "p1-b"}

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.portfolio_created" in emitted_types
    assert "coordinator.portfolio_ranked" in emitted_types


def test_coordinator_portfolio_blocked_and_risk_detected() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-6"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-6"

    seed = [
        {
            "objective_id": "pb-a",
            "title": "blocked source",
            "priority": 8,
            "salience": 8.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "pb-b",
            "title": "blocked dependent",
            "priority": 9,
            "salience": 9.0,
            "depends_on": ["pb-a"],
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]
    for payload in seed:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "portfolio blocked risk pass",
        },
    )
    assert analyze.status_code == 200

    emitted_types = [event["event_type"] for event in analyze.json()["emitted_events"]]
    assert "coordinator.portfolio_blocked" in emitted_types
    assert "coordinator.portfolio_risk_detected" in emitted_types


def test_coordinator_portfolio_health_updated_contains_normalized_scores() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-7"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-7"

    for payload in [
        {
            "objective_id": "ph-a",
            "title": "portfolio health a",
            "priority": 5,
            "salience": 5.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "ph-b",
            "title": "portfolio health b",
            "priority": 4,
            "salience": 4.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "portfolio health calculation",
        },
    )
    assert analyze.status_code == 200

    health_rows = analyze.json()["portfolio_health"]
    assert len(health_rows) >= 1
    for row in health_rows:
        assert 0.0 <= float(row["portfolio_health"]) <= 1.0
        assert 0.0 <= float(row["portfolio_risk"]) <= 1.0


def test_coordinator_portfolio_recommends_governance_review_when_escalated() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-8"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-8"

    for payload in [
        {
            "objective_id": "pg-a",
            "title": "portfolio governance a",
            "priority": 8,
            "salience": 8.0,
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "pg-b",
            "title": "portfolio governance b",
            "priority": 8,
            "salience": 8.0,
            "depends_on": ["pg-a"],
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    GOVERNANCE_STATES[workspace_id] = {
        "updated_at": "2026-01-01T00:00:00+00:00",
        "band": "escalated",
        "interrupt_sensitivity": 0.8,
        "escalation_readiness": 0.9,
        "cooldown_aggressiveness": 0.3,
        "posture_persistence": 0.8,
        "governance_attention": 0.9,
        "confidence": 0.7,
        "profile": "mission_critical",
    }

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "portfolio governance recommendation",
        },
    )
    assert analyze.status_code == 200

    actions = analyze.json()["recommended_actions"]
    portfolio_governance = [
        action
        for action in actions
        if action.get("type") == "portfolio_risk_detected" and action.get("action") == "governance_review_portfolio"
    ]
    assert len(portfolio_governance) >= 1


def test_coordinator_cross_portfolio_dependency_detected() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-9"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-9"

    seed = [
        {
            "objective_id": "cp-beta",
            "title": "beta foundation",
            "priority": 8,
            "salience": 8.0,
            "portfolio_group": "beta",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "cp-alpha",
            "title": "alpha dependent",
            "priority": 6,
            "salience": 6.0,
            "depends_on": ["cp-beta"],
            "portfolio_group": "alpha",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]
    for payload in seed:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "cross-portfolio dependency check",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    assert len(body["cross_portfolio_dependencies"]) >= 1
    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.portfolio_dependency_detected" in emitted_types

    dependency_actions = [a for a in body["recommended_actions"] if a.get("type") == "portfolio_dependency_detected"]
    assert len(dependency_actions) >= 1


def test_coordinator_portfolio_resource_conflict_detected() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-10"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-10"

    for payload in [
        {
            "objective_id": "rc-a",
            "title": "resource contender a",
            "priority": 8,
            "salience": 8.0,
            "portfolio_group": "ra",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "rc-b",
            "title": "resource contender b",
            "priority": 7,
            "salience": 7.0,
            "portfolio_group": "rb",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    for task_id, objective_id in (("rc-wf-a", "rc-a"), ("rc-wf-b", "rc-b")):
        arb = client.post(
            "/api/agents/arbitrate",
            json={
                "task_id": task_id,
                "objective_id": objective_id,
                "operator_forced_role": "execution",
                "workspace_id": workspace_id,
                "execution_id": execution_id,
            },
        )
        assert arb.status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "resource conflict check",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    assert len(body["portfolio_resource_conflicts"]) >= 1
    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.portfolio_resource_conflict_detected" in emitted_types

    conflict_actions = [a for a in body["recommended_actions"] if a.get("type") == "portfolio_resource_conflict_detected"]
    assert len(conflict_actions) >= 1


def test_coordinator_portfolio_priority_change_event_emitted_on_reorder() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-11"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-11"

    seed = [
        {
            "objective_id": "pc-a",
            "title": "priority alpha",
            "priority": 4,
            "salience": 4.0,
            "portfolio_group": "pa",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "pc-b",
            "title": "priority beta",
            "priority": 8,
            "salience": 8.0,
            "portfolio_group": "pb",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]
    for payload in seed:
        assert client.post("/api/objectives", json=payload).status_code == 200

    first = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "baseline ranking",
        },
    )
    assert first.status_code == 200
    first_types = [event["event_type"] for event in first.json()["emitted_events"]]
    assert "coordinator.portfolio_priority_changed" not in first_types

    flip = client.post(
        "/api/objectives",
        json={
            "objective_id": "pc-a",
            "title": "priority alpha",
            "priority": 10,
            "salience": 10.0,
            "portfolio_group": "pa",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    )
    assert flip.status_code == 200

    second = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "ranking after pressure shift",
        },
    )
    assert second.status_code == 200
    body = second.json()

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.portfolio_priority_changed" in emitted_types
    assert any(a.get("type") == "portfolio_priority_changed" for a in body["recommended_actions"])


def test_coordinator_portfolio_escalation_and_suspension_recommendations() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-12"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-12"

    seed = [
        {
            "objective_id": "ps-anchor",
            "title": "high pressure anchor",
            "priority": 10,
            "salience": 10.0,
            "portfolio_group": "anchor",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "ps-low",
            "title": "low pressure blocked",
            "priority": 1,
            "salience": 1.0,
            "depends_on": ["ps-anchor"],
            "portfolio_group": "fragile",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]
    for payload in seed:
        assert client.post("/api/objectives", json=payload).status_code == 200

    GOVERNANCE_STATES[workspace_id] = {
        "updated_at": "2026-01-01T00:00:00+00:00",
        "band": "escalated",
        "interrupt_sensitivity": 0.8,
        "escalation_readiness": 0.9,
        "cooldown_aggressiveness": 0.3,
        "posture_persistence": 0.8,
        "governance_attention": 0.9,
        "confidence": 0.7,
        "profile": "mission_critical",
    }

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "portfolio escalation and suspension checks",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    actions = body["recommended_actions"]
    assert any(a.get("type") == "portfolio_escalation_recommended" for a in actions)
    assert not any(a.get("type") == "portfolio_suspension_recommended" for a in actions)

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.portfolio_escalation_recommended" in emitted_types
    assert "coordinator.portfolio_recommendation_suppressed" in emitted_types


def test_coordinator_portfolio_policy_applied_event_emitted() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-13"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)
    GOVERNANCE_PROFILE_BINDINGS[workspace_id] = {
        "active": "mission_critical",
        "overrides": {},
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    client = TestClient(app)
    execution_id = "exec-coordinator-13"

    assert (
        client.post(
            "/api/objectives",
            json={
                "objective_id": "pp-a",
                "title": "policy profile objective",
                "priority": 9,
                "salience": 9.0,
                "portfolio_group": "pp",
                "workspace_id": workspace_id,
                "execution_id": execution_id,
            },
        ).status_code
        == 200
    )

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "portfolio policy apply signal",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    assert body["portfolio_policy"]["active_profile"] == "mission_critical"
    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.portfolio_policy_applied" in emitted_types


def test_coordinator_portfolio_policy_requires_governance_review_before_escalation() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-14"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)
    GOVERNANCE_PROFILE_BINDINGS[workspace_id] = {
        "active": "conservative",
        "overrides": {},
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    client = TestClient(app)
    execution_id = "exec-coordinator-14"

    seed = [
        {
            "objective_id": "gr-anchor",
            "title": "anchor",
            "priority": 10,
            "salience": 10.0,
            "portfolio_group": "core",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "gr-risk",
            "title": "high-risk dependent",
            "priority": 10,
            "salience": 10.0,
            "depends_on": ["gr-anchor"],
            "portfolio_group": "compliance",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]
    for payload in seed:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "governance review gate",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    assert any(
        a.get("type") == "portfolio_escalation_recommended" and a.get("action") == "governance_review_portfolio"
        for a in body["recommended_actions"]
    )
    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.portfolio_governance_review_required" in emitted_types


def test_coordinator_portfolio_policy_conflict_suppresses_suspension() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-15"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)
    GOVERNANCE_PROFILE_BINDINGS[workspace_id] = {
        "active": "balanced",
        "overrides": {},
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    client = TestClient(app)
    execution_id = "exec-coordinator-15"

    seed = [
        {
            "objective_id": "pcf-anchor",
            "title": "anchor",
            "priority": 10,
            "salience": 10.0,
            "portfolio_group": "core",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "pcf-risk",
            "title": "conflict candidate",
            "priority": 3,
            "salience": 3.0,
            "depends_on": ["pcf-anchor"],
            "portfolio_group": "fragile",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]
    for payload in seed:
        assert client.post("/api/objectives", json=payload).status_code == 200

    GOVERNANCE_STATES[workspace_id] = {
        "updated_at": "2026-01-01T00:00:00+00:00",
        "band": "warning",
        "interrupt_sensitivity": 0.7,
        "escalation_readiness": 0.8,
        "cooldown_aggressiveness": 0.4,
        "posture_persistence": 0.7,
        "governance_attention": 0.8,
        "confidence": 0.8,
        "profile": "balanced",
    }

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "policy conflict arbitration",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    assert any(a.get("type") == "portfolio_escalation_recommended" for a in body["recommended_actions"])
    assert not any(a.get("type") == "portfolio_suspension_recommended" for a in body["recommended_actions"])
    assert len(body["portfolio_policy_conflicts"]) >= 1
    assert len(body["portfolio_suppressed_recommendations"]) >= 1

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.portfolio_policy_conflict_detected" in emitted_types
    assert "coordinator.portfolio_recommendation_suppressed" in emitted_types


def test_coordinator_intent_candidates_created_for_portfolio_recommendations() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-16"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-16"

    seed = [
        {
            "objective_id": "ic-beta",
            "title": "beta base",
            "priority": 8,
            "salience": 8.0,
            "portfolio_group": "beta",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "ic-alpha",
            "title": "alpha dependent",
            "priority": 6,
            "salience": 6.0,
            "depends_on": ["ic-beta"],
            "portfolio_group": "alpha",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]
    for payload in seed:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "intent candidate generation",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    assert len(body["intent_candidates"]) >= 1
    assert all(candidate.get("advisory") is True for candidate in body["intent_candidates"])
    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.intent_candidate_created" in emitted_types
    assert "coordinator.intent_promotion_requested" in emitted_types


def test_coordinator_intent_promotion_denied_in_escalated_band_for_non_review_actions() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-17"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-coordinator-17"

    for payload in [
        {
            "objective_id": "id-beta",
            "title": "beta base",
            "priority": 8,
            "salience": 8.0,
            "portfolio_group": "beta",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "id-alpha",
            "title": "alpha dependent",
            "priority": 6,
            "salience": 6.0,
            "depends_on": ["id-beta"],
            "portfolio_group": "alpha",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    GOVERNANCE_STATES[workspace_id] = {
        "updated_at": "2026-01-01T00:00:00+00:00",
        "band": "escalated",
        "interrupt_sensitivity": 0.8,
        "escalation_readiness": 0.9,
        "cooldown_aggressiveness": 0.3,
        "posture_persistence": 0.8,
        "governance_attention": 0.9,
        "confidence": 0.7,
        "profile": "mission_critical",
    }

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "intent promotion denial path",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    assert any(decision.get("status") == "denied" for decision in body["intent_promotions"])
    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.intent_promotion_denied" in emitted_types


def test_coordinator_intent_promotion_approved_for_governance_review_action() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-coordinator-18"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)
    GOVERNANCE_PROFILE_BINDINGS[workspace_id] = {
        "active": "conservative",
        "overrides": {},
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    client = TestClient(app)
    execution_id = "exec-coordinator-18"

    for payload in [
        {
            "objective_id": "ia-anchor",
            "title": "anchor",
            "priority": 10,
            "salience": 10.0,
            "portfolio_group": "core",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "ia-risk",
            "title": "risk",
            "priority": 10,
            "salience": 10.0,
            "depends_on": ["ia-anchor"],
            "portfolio_group": "compliance",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "intent promotion approval path",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    approved_governance = [
        decision
        for decision in body["intent_promotions"]
        if decision.get("status") == "approved" and decision.get("action", "").startswith("governance_review")
    ]
    assert len(approved_governance) >= 1

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "coordinator.intent_promotion_approved" in emitted_types


def test_supervisor_intake_acknowledges_approved_intent() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-supervisor-intent-1"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)
    SUPERVISOR_INTENTS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-supervisor-intent-1"

    for payload in [
        {
            "objective_id": "si-beta",
            "title": "beta",
            "priority": 8,
            "salience": 8.0,
            "portfolio_group": "beta",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "si-alpha",
            "title": "alpha",
            "priority": 6,
            "salience": 6.0,
            "depends_on": ["si-beta"],
            "portfolio_group": "alpha",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "supervisor intake ack",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "supervisor.intent_received" in emitted_types

    pending = [intent for intent in body["supervisor_intents"] if intent.get("status") == "pending"]
    assert len(pending) >= 1

    ack = client.post(
        f"/api/supervisor/intents/{pending[0]['intent_id']}/status",
        json={
            "status": "acknowledged",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "accepted for intake queue",
        },
    )
    assert ack.status_code == 200
    assert ack.json()["event"]["event_type"] == "supervisor.intent_acknowledged"


def test_supervisor_intake_reject_requires_reason_code() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-supervisor-intent-2"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)
    SUPERVISOR_INTENTS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-supervisor-intent-2"

    for payload in [
        {
            "objective_id": "sr-beta",
            "title": "beta",
            "priority": 8,
            "salience": 8.0,
            "portfolio_group": "beta",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "sr-alpha",
            "title": "alpha",
            "priority": 6,
            "salience": 6.0,
            "depends_on": ["sr-beta"],
            "portfolio_group": "alpha",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "supervisor intake reject",
        },
    )
    assert analyze.status_code == 200
    pending = [intent for intent in analyze.json()["supervisor_intents"] if intent.get("status") == "pending"]
    assert len(pending) >= 1

    reject_missing_code = client.post(
        f"/api/supervisor/intents/{pending[0]['intent_id']}/status",
        json={
            "status": "rejected",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "missing code",
        },
    )
    assert reject_missing_code.status_code == 400

    reject = client.post(
        f"/api/supervisor/intents/{pending[0]['intent_id']}/status",
        json={
            "status": "rejected",
            "reason_code": "portfolio_conflict",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "portfolio overlap",
        },
    )
    assert reject.status_code == 200
    assert reject.json()["event"]["event_type"] == "supervisor.intent_rejected"


def test_supervisor_intake_expiry_transition_emits_event() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-supervisor-intent-3"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)
    SUPERVISOR_INTENTS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-supervisor-intent-3"

    for payload in [
        {
            "objective_id": "se-beta",
            "title": "beta",
            "priority": 8,
            "salience": 8.0,
            "portfolio_group": "beta",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "se-alpha",
            "title": "alpha",
            "priority": 6,
            "salience": 6.0,
            "depends_on": ["se-beta"],
            "portfolio_group": "alpha",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "supervisor intake expiry",
        },
    )
    assert analyze.status_code == 200
    pending = [intent for intent in analyze.json()["supervisor_intents"] if intent.get("status") == "pending"]
    assert len(pending) >= 1

    expire = client.post(
        f"/api/supervisor/intents/{pending[0]['intent_id']}/status",
        json={
            "status": "expired",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "timed out",
        },
    )
    assert expire.status_code == 200
    assert expire.json()["event"]["event_type"] == "supervisor.intent_expired"


def test_supervisor_intake_replay_integrity_links_promotion_event() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-supervisor-intent-4"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)
    SUPERVISOR_INTENTS_BY_WORKSPACE.pop(workspace_id, None)

    client = TestClient(app)
    execution_id = "exec-supervisor-intent-4"

    for payload in [
        {
            "objective_id": "sl-beta",
            "title": "beta",
            "priority": 8,
            "salience": 8.0,
            "portfolio_group": "beta",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "sl-alpha",
            "title": "alpha",
            "priority": 6,
            "salience": 6.0,
            "depends_on": ["sl-beta"],
            "portfolio_group": "alpha",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "replay linkage",
        },
    )
    assert analyze.status_code == 200
    pending = [intent for intent in analyze.json()["supervisor_intents"] if intent.get("status") == "pending"]
    assert len(pending) >= 1
    promotion_event_id = str(pending[0]["promotion_event_id"])

    ack = client.post(
        f"/api/supervisor/intents/{pending[0]['intent_id']}/status",
        json={
            "status": "acknowledged",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "ready",
        },
    )
    assert ack.status_code == 200

    replay = client.get(f"/api/replay/{execution_id}")
    assert replay.status_code == 200
    events = replay.json()["events"]

    approved_matches = [
        event
        for event in events
        if event.get("event_type") == "coordinator.intent_promotion_approved" and event.get("event_id") == promotion_event_id
    ]
    assert len(approved_matches) == 1

    received_matches = [
        event
        for event in events
        if event.get("event_type") == "supervisor.intent_received"
        and str(event.get("payload", {}).get("promotion_event_id", "")) == promotion_event_id
    ]
    assert len(received_matches) >= 1

    replay_types = [event["event_type"] for event in events]
    assert "coordinator.recommendation_created" in replay_types
    assert "coordinator.intent_candidate_created" in replay_types
    assert "coordinator.intent_promotion_requested" in replay_types
    assert "coordinator.intent_promotion_approved" in replay_types
    assert "supervisor.intent_received" in replay_types
    assert "supervisor.intent_acknowledged" in replay_types


def test_governance_review_started_and_approved_before_supervisor_receives_intent() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-governance-review-1"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)
    SUPERVISOR_INTENTS_BY_WORKSPACE.pop(workspace_id, None)
    GOVERNANCE_PROFILE_BINDINGS[workspace_id] = {
        "active": "conservative",
        "overrides": {},
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    client = TestClient(app)
    execution_id = "exec-governance-review-1"

    for payload in [
        {
            "objective_id": "gr1-anchor",
            "title": "anchor",
            "priority": 10,
            "salience": 10.0,
            "portfolio_group": "core",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "gr1-risk",
            "title": "risk",
            "priority": 10,
            "salience": 10.0,
            "depends_on": ["gr1-anchor"],
            "portfolio_group": "compliance",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "governance review approval chain",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "governance.intent_review_started" in emitted_types
    assert "governance.intent_review_approved" in emitted_types
    assert "supervisor.intent_received" in emitted_types


def test_governance_review_denied_blocks_supervisor_intake() -> None:
    OBJECTIVES.clear()
    OBJECTIVE_SIGNALS["blocked"] = {}
    OBJECTIVE_SIGNALS["pressure"] = {}
    OBJECTIVE_SIGNALS["objective_pressure_score"] = {}
    OBJECTIVE_SIGNALS["critical_path"] = {}

    workspace_id = "ws-governance-review-2"
    AGENT_TASKS_BY_WORKSPACE.pop(workspace_id, None)
    AGENT_WORKFLOWS_BY_WORKSPACE.pop(workspace_id, None)
    SUPERVISOR_INTENTS_BY_WORKSPACE.pop(workspace_id, None)
    GOVERNANCE_PROFILE_BINDINGS[workspace_id] = {
        "active": "aggressive",
        "overrides": {},
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    client = TestClient(app)
    execution_id = "exec-governance-review-2"

    for payload in [
        {
            "objective_id": "gr2-beta",
            "title": "beta",
            "priority": 8,
            "salience": 8.0,
            "portfolio_group": "beta",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
        {
            "objective_id": "gr2-alpha",
            "title": "alpha",
            "priority": 6,
            "salience": 6.0,
            "depends_on": ["gr2-beta"],
            "portfolio_group": "alpha",
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        },
    ]:
        assert client.post("/api/objectives", json=payload).status_code == 200

    GOVERNANCE_STATES[workspace_id] = {
        "updated_at": "2026-01-01T00:00:00+00:00",
        "band": "escalated",
        "interrupt_sensitivity": 0.8,
        "escalation_readiness": 0.9,
        "cooldown_aggressiveness": 0.3,
        "posture_persistence": 0.8,
        "governance_attention": 0.9,
        "confidence": 0.7,
        "profile": "aggressive",
    }

    analyze = client.post(
        "/api/coordinator/analyze",
        json={
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "reason": "governance review denial chain",
        },
    )
    assert analyze.status_code == 200
    body = analyze.json()

    emitted_types = [event["event_type"] for event in body["emitted_events"]]
    assert "governance.intent_review_started" in emitted_types
    assert "governance.intent_review_denied" in emitted_types

    denied_reviews = [review for review in body["governance_intent_reviews"] if review.get("status") == "denied"]
    assert len(denied_reviews) >= 1
    assert all(review.get("reason_code") == "governance_mismatch" for review in denied_reviews)

    denied_intent_ids = {str(review.get("intent_id")) for review in denied_reviews}
    received_for_denied = [
        event
        for event in body["emitted_events"]
        if event.get("event_type") == "supervisor.intent_received"
        and str(event.get("payload", {}).get("intent", {}).get("intent_id", "")) in denied_intent_ids
    ]
    assert len(received_for_denied) == 0
