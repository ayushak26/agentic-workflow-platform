from __future__ import annotations

from scripts.check_api_routes import (
    REQUIRED_ROUTES,
    missing_required_routes,
    registered_routes,
)


def test_studio_backend_routes_are_registered():
    """Prevent shipping a UI whose FastAPI endpoints were not mounted."""

    missing = missing_required_routes()

    assert not missing, (
        "Frontend/backend route mismatch. Missing: "
        + ", ".join(
            f"{method} {path}"
            for method, path in sorted(missing)
        )
    )


def test_route_contract_contains_preflight_and_file_inputs():
    assert ("POST", "/api/workflows/validate") in REQUIRED_ROUTES
    assert (
        "GET",
        "/api/workflow-input-files/capabilities",
    ) in REQUIRED_ROUTES
    assert ("GET", "/api/llm/models") in REQUIRED_ROUTES
    assert (
        "POST",
        "/api/llm/models/{model}/probe",
    ) in REQUIRED_ROUTES


def test_removed_business_and_pipeline_apis_are_not_registered():
    paths = {path for _, path in registered_routes()}

    assert not any(path.startswith("/api/pipelines") for path in paths)
    assert not any("/business-" in path for path in paths)
    assert not any(path.endswith("/fact-correction") for path in paths)
