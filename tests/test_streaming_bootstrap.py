from fastapi.testclient import TestClient

from main import GOVERNANCE_PROFILE_STATE, GOVERNANCE_STATE, OBJECTIVES, OBJECTIVE_SIGNALS, TRUST_STATE, app


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
            },
        )
        assert fail.status_code == 200

    gov = client.post("/api/governance/recompute", json={"execution_id": execution_id})
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

    a = client.post(
        "/api/trust/recompute",
        json={"trust_score": 0.2, "execution_id": execution_id},
    )
    assert a.status_code == 200

    b = client.post(
        "/api/trust/recompute",
        json={"trust_score": 0.9, "execution_id": execution_id},
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
