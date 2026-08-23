"""Cross-user authorization: User A must never reach User B's runs.

Runs are scoped by the caller's session (the JWT subject). These tests
mint real tokens for two users and exercise the actual HTTP surface
against an in-memory run store, asserting both the read and the
destructive paths refuse cross-user access — and that a client-supplied
session_id cannot override the token's scope.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security.jwt_handler import create_access_token
from app.workflow.run_history import upsert_run
from tests.fake_mongo import InMemoryDB

ALICE_RUN = "run-alice-0001"
BOB_RUN = "run-bob-0001"


@pytest.fixture(scope="module", autouse=True)
def _unthrottled():
    """Same exemption pattern as test_builder_api.py — keep the shared
    per-minute budget out of these tests' way."""
    from app.config import settings

    original = settings.rate_limit_enabled
    settings.rate_limit_enabled = False
    yield
    settings.rate_limit_enabled = original


def _token(username: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token({
            'sub': username,
            'role': 'consultant',
            'session_id': username,
        })}"
    }


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as instance:
        yield instance


@pytest.fixture()
async def seeded_db(client):
    """Swap in an isolated in-memory run store seeded with one run per user."""
    db = InMemoryDB()
    await upsert_run(
        db, ALICE_RUN, "alice",
        workflow_name="alice-private-workflow", status="completed",
        inputs={}, node_count=1, completed_node_count=1,
    )
    await upsert_run(
        db, BOB_RUN, "bob",
        workflow_name="bob-private-workflow", status="completed",
        inputs={}, node_count=1, completed_node_count=1,
    )
    services = getattr(app.state, "services", None)
    assert services is not None
    original = services.get("audit_db")
    services["audit_db"] = db
    yield db
    services["audit_db"] = original


def test_a_user_lists_only_their_own_runs(client, seeded_db):
    body = client.get("/api/runs/mine", headers=_token("bob")).json()
    run_ids = [run["run_id"] for run in body["runs"]]
    assert BOB_RUN in run_ids
    assert ALICE_RUN not in run_ids


def test_a_user_cannot_read_another_users_run(client, seeded_db):
    response = client.get(f"/api/runs/mine/{ALICE_RUN}", headers=_token("bob"))
    assert response.status_code == 404


def test_a_user_cannot_delete_another_users_run(client, seeded_db):
    response = client.delete(
        f"/api/runs/mine/{ALICE_RUN}", headers=_token("bob")
    )
    assert response.status_code == 404
    # The record must survive the attempt, untouched.
    assert client.get(
        f"/api/runs/mine/{ALICE_RUN}", headers=_token("alice")
    ).status_code == 200


def test_a_client_supplied_session_cannot_override_the_token_scope(
    client, seeded_db
):
    """POST /workflows/{run_id}/resume derives its scope from the token and
    refuses when the body tries to claim someone else's session (400 with a
    clear detail — the override never reaches storage)."""
    response = client.post(
        f"/api/workflows/{ALICE_RUN}/resume",
        headers=_token("bob"),
        json={"decision": {"decision": "approve"}, "session_id": "alice"},
    )
    assert response.status_code == 400
    assert "session_id must match" in response.json()["detail"]


def test_unauthenticated_access_is_refused(client, seeded_db):
    assert client.get("/api/runs/mine").status_code == 401
    assert client.get(f"/api/runs/mine/{BOB_RUN}").status_code == 401
    assert client.delete(f"/api/runs/mine/{BOB_RUN}").status_code == 401
