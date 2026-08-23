from __future__ import annotations

import sys

import pytest

import scripts.smoke_production as smoke


SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Request-ID": "request-1",
}


def _run(monkeypatch, statuses: dict[str, int]) -> None:
    def fake_request(url: str):
        path = "/" + url.split("/", 3)[-1]
        status = statuses[path]
        headers = SECURITY_HEADERS if path == "/health" else {}
        return status, headers, b"{}"

    monkeypatch.setattr(smoke, "request", fake_request)
    monkeypatch.setattr(
        sys,
        "argv",
        ["smoke_production.py", "--base-url", "https://ai.example.com"],
    )
    smoke.main()


def _healthy_statuses() -> dict[str, int]:
    return {
        "/health": 200,
        "/ready": 200,
        "/docs": 404,
        "/metrics": 404,
        "/api/runs/mine": 401,
        "/api/pipelines": 404,
        "/api/runs/mine/removed-smoke-run/business-projection": 404,
    }


def test_production_smoke_accepts_canonical_api_and_removed_routes(monkeypatch):
    _run(monkeypatch, _healthy_statuses())


@pytest.mark.parametrize(
    ("path", "status", "message"),
    [
        ("/api/runs/mine", 404, "should be mounted and authentication-protected"),
        ("/api/pipelines", 401, "/api/pipelines should be removed"),
        (
            "/api/runs/mine/removed-smoke-run/business-projection",
            401,
            "Removed Business run endpoint should return 404",
        ),
    ],
)
def test_production_smoke_rejects_route_contract_regressions(
    monkeypatch, path, status, message,
):
    statuses = _healthy_statuses()
    statuses[path] = status

    with pytest.raises(SystemExit, match=message):
        _run(monkeypatch, statuses)