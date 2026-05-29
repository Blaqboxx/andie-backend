from fastapi.testclient import TestClient

from main import app


def test_connection_ready_then_workspace_snapshot_sequence() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/stream") as ws:
        first = ws.receive_json()
        second = ws.receive_json()

    assert first["type"] == "connection.ready"
    assert second["type"] == "workspace.snapshot"
    assert second["seq"] > first["seq"]


def test_alias_route_normalization_matches_canonical_bootstrap() -> None:
    client = TestClient(app)

    with client.websocket_connect("/ws/stream") as ws:
        canonical = [ws.receive_json(), ws.receive_json()]

    with client.websocket_connect("/ws/backlog") as ws:
        alias = [ws.receive_json(), ws.receive_json()]

    assert canonical[0]["type"] == alias[0]["type"] == "connection.ready"
    assert canonical[1]["type"] == alias[1]["type"] == "workspace.snapshot"


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
    assert body["events"][-1]["payload"]["to"] == "warming"


def test_websocket_clean_close() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/events") as ws:
        _ = ws.receive_json()
        _ = ws.receive_json()
        ws.close()
