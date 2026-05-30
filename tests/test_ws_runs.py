"""End-to-end test of the WebSocket event stream.

Enabled in Phase 9B once the run endpoint accepts a client-supplied run_id.
The race we need to fix: client must subscribe to the WS *before* events fire,
which means generating run_id on the client and passing it into POST /run.
"""
import uuid
import pytest
from fastapi.testclient import TestClient


HELLO_YAML = """..."""  # same as the integration tests


@pytest.mark.skip(reason="enabled in Phase 9B once run-by-run_id is wired")
def test_ws_receives_events_for_running_workflow():
    from app.main import app
    client = TestClient(app)
    run_id = str(uuid.uuid4())

    # Subscribe first, then trigger — avoids the race where events fire before subscribe.
    with client.websocket_connect(f"/api/ws/runs/{run_id}") as ws:
        r = client.post("/api/workflows/run", json={
            "workflow_yaml": HELLO_YAML,
            "inputs": {"text": "hi"},
            "run_id": run_id,
        })
        assert r.status_code == 200

        events = []
        while True:
            evt = ws.receive_json()
            events.append(evt)
            if evt["type"] in ("run_completed", "run_failed"):
                break

    types = [e["type"] for e in events]
    assert "node_started" in types
    assert "node_completed" in types
    assert types[-1] == "run_completed"