"""Phase 1 smoke test: confirm the FastAPI app boots and /health responds."""
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    """/health is the liveness probe. It must return 200 and {"status": "ok"}."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}