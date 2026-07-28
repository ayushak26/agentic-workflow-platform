import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    # The 'with' block triggers FastAPI lifespan startup/shutdown,
    # so app.state.services is populated before requests run.
    with TestClient(app) as c:
        yield c


def test_health_returns_ok(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] in {"ok", "degraded"}
    assert isinstance(res.json()["ready"], bool)
    assert "mongo" in res.json()["services"]


def test_ready_is_registered_and_truthful(client):
    res = client.get("/ready")
    assert res.status_code in {200, 503}
    assert res.json()["ready"] is (res.status_code == 200)


def test_login_dev_bypass(client):
    res = client.post("/auth/token", data={"username": "ayush", "password": "dev123"})
    assert res.status_code == 200
    body = res.json()
    assert "access_token" in body
    assert body["role"] == "admin"


def test_protected_route_without_token(client):
    res = client.get("/api/cost/run/some-run-id")
    assert res.status_code == 401
