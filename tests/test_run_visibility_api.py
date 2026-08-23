from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.runs import router
from app.security.dependencies import CurrentUser, require_consultant
from app.security.rbac import Role
from tests.fake_mongo import InMemoryDB


def _client_with_runs() -> TestClient:
    db = InMemoryDB()
    now = datetime.now(timezone.utc)
    db["run_history"].docs.extend([
        {
            "run_id": "global-run", "session_id": "tenant-a",
            "workflow_name": "Shared workflow", "status": "completed",
            "origin": "direct", "history_visibility": "global",
            "created_at": now, "updated_at": now,
        },
        {
            "run_id": "chat-run", "session_id": "tenant-a",
            "workflow_name": "Shared workflow", "status": "completed",
            "origin": "chat_saved_workflow",
            "history_visibility": "conversation_only",
            "workflow_id": "research-helper", "workflow_version_id": "version-3",
            "conversation_id": "conversation-1", "message_id": "message-1",
            "created_at": now, "updated_at": now,
        },
    ])
    app = FastAPI()
    app.state.services = {"audit_db": db}
    app.include_router(router)
    app.dependency_overrides[require_consultant] = lambda: CurrentUser(
        "alice", Role.CONSULTANT, session_id="tenant-a",
    )
    return TestClient(app)


def test_conversation_only_run_is_hidden_from_list_but_available_by_id():
    with _client_with_runs() as client:
        listed = client.get("/api/runs/mine")
        detail = client.get("/api/runs/mine/chat-run")

    assert listed.status_code == 200
    assert [run["run_id"] for run in listed.json()["runs"]] == ["global-run"]
    assert detail.status_code == 200
    run = detail.json()["run"]
    assert run["origin"] == "chat_saved_workflow"
    assert run["history_visibility"] == "conversation_only"
    assert run["workflow_id"] == "research-helper"
    assert run["workflow_version_id"] == "version-3"
    assert run["conversation_id"] == "conversation-1"
    assert run["message_id"] == "message-1"


def test_run_history_limit_is_bounded():
    with _client_with_runs() as client:
        response = client.get("/api/runs/mine?limit=201")
    assert response.status_code == 422