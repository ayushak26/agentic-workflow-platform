"""Authenticated Server-Sent Events transport tests."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.workflows import _sse_message, router
from app.runtime.events import RunEvent, RunEventBus
from app.security.dependencies import CurrentUser, require_consultant
from app.security.rbac import Role


@pytest.mark.asyncio
async def test_event_bus_is_session_scoped_and_replays_after_id():
    bus = RunEventBus(max_events_per_run=10)
    await bus.publish(
        RunEvent(
            type="node_started",
            run_id="run-1",
            session_id="tenant-a",
            node_id="search",
        )
    )
    await bus.publish(
        RunEvent(
            type="node_completed",
            run_id="run-1",
            session_id="tenant-a",
            node_id="search",
            output_preview="done",
        )
    )

    tenant_a = await bus.subscribe(
        "run-1",
        "tenant-a",
        after_event_id=1,
    )
    tenant_b = await bus.subscribe("run-1", "tenant-b")

    replayed = tenant_a.get_nowait()
    assert replayed.type == "node_completed"
    assert replayed.event_id == 2
    assert tenant_b.empty()


def test_sse_message_uses_standard_wire_format():
    encoded = _sse_message(
        event="node_started",
        event_id=7,
        data={"type": "node_started", "run_id": "run-1"},
    )
    assert encoded.startswith("id: 7\nevent: node_started\n")
    assert 'data: {"type":"node_started","run_id":"run-1"}' in encoded
    assert encoded.endswith("\n\n")


@pytest.mark.asyncio
async def test_sse_endpoint_replays_terminal_event_with_auth():
    app = FastAPI()
    app.include_router(router)
    bus = RunEventBus()
    app.state.services = {
        "event_bus": bus,
        "sse_heartbeat_seconds": 0.05,
    }
    app.dependency_overrides[require_consultant] = lambda: CurrentUser(
        "alice",
        Role.CONSULTANT,
        session_id="tenant-a",
    )
    await bus.publish(
        RunEvent(
            type="run_completed",
            run_id="run-1",
            session_id="tenant-a",
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/runs/run-1/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "text/event-stream"
    )
    assert response.headers["x-accel-buffering"] == "no"
    assert "event: ready" in response.text
    assert "event: run_completed" in response.text
    assert '"session_id"' not in response.text
