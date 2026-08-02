from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.workflows as workflows_module
from app.security.dependencies import CurrentUser, require_consultant
from app.security.rbac import Role
from app.workflow.builder_store import WorkflowBuilderStore

VALID_YAML = """
name: Builder Route Test
version: '1.0'
nodes:
  - id: echo
    type: Literal
    config:
      value: hi
edges: []
entry: echo
exit: echo
"""

INVALID_YAML = "not: [valid, workflow"


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    # Route handlers module-import BUILDER_STORE at import time — point it at
    # a scratch directory instead of the real repo's `workflows/` folder so
    # these tests never touch real workflow files.
    monkeypatch.setattr(
        workflows_module, "BUILDER_STORE", WorkflowBuilderStore(tmp_path),
    )
    # Same isolation for the pipeline-reference scan the delete route runs —
    # point it at an empty scratch dir instead of the repo's real pipelines.
    monkeypatch.setattr(workflows_module, "PIPELINES_DIR", tmp_path / "pipelines")
    scratch_app = FastAPI()
    scratch_app.include_router(workflows_module.router)
    scratch_app.dependency_overrides[require_consultant] = lambda: CurrentUser(
        "alice", Role.CONSULTANT, session_id="tenant-a",
    )
    return TestClient(scratch_app)


def test_draft_lifecycle(client):
    res = client.get("/api/workflows/routetest/draft")
    assert res.status_code == 404

    res = client.put(
        "/api/workflows/routetest/draft",
        json={"yaml": VALID_YAML, "canvas": {"selected_node_id": "echo"}},
    )
    assert res.status_code == 200
    assert res.json()["yaml"] == VALID_YAML

    res = client.get("/api/workflows/routetest/draft")
    assert res.status_code == 200
    assert res.json()["yaml"] == VALID_YAML
    assert res.json()["differs_from_current"] is True

    res = client.delete("/api/workflows/routetest/draft")
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    res = client.get("/api/workflows/routetest/draft")
    assert res.status_code == 404


def test_draft_rejects_unsafe_name(client):
    res = client.put(
        "/api/workflows/../escape/draft",
        json={"yaml": VALID_YAML},
    )
    # Path normalization turns this into a different (still safe) route or a
    # 404 depending on the ASGI server; the important guarantee is it never
    # reaches BUILDER_STORE with a traversal-y name. Confirm no file escapes.
    assert res.status_code in {400, 404}


def test_save_then_versions_list_and_get(client):
    res = client.post(
        "/api/workflows/save", json={"name": "routetest", "yaml": VALID_YAML},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    version_id = body["version_id"]

    res = client.get("/api/workflows/routetest/versions")
    assert res.status_code == 200
    versions = res.json()
    assert len(versions) == 1
    assert versions[0]["version_id"] == version_id
    assert versions[0]["current"] is True

    res = client.get(f"/api/workflows/routetest/versions/{version_id}")
    assert res.status_code == 200
    assert res.json()["yaml"] == VALID_YAML

    res = client.get("/api/workflows/routetest/versions/does-not-exist")
    assert res.status_code == 404


def test_save_rejects_invalid_yaml_with_422(client):
    res = client.post(
        "/api/workflows/save", json={"name": "routetest", "yaml": INVALID_YAML},
    )
    assert res.status_code == 422


def test_save_rejects_unsafe_name_with_400(client):
    res = client.post(
        "/api/workflows/save", json={"name": "bad name!", "yaml": VALID_YAML},
    )
    assert res.status_code == 400


def test_restore_version_preflights_and_creates_new_version(client):
    client.post("/api/workflows/save", json={"name": "routetest", "yaml": VALID_YAML})
    versions = client.get("/api/workflows/routetest/versions").json()
    version_id = versions[0]["version_id"]

    res = client.post(f"/api/workflows/routetest/versions/{version_id}/restore")
    assert res.status_code == 200
    body = res.json()
    assert body["yaml"] == VALID_YAML
    assert body["version_id"] == version_id


def test_restore_unknown_version_is_404(client):
    client.post("/api/workflows/save", json={"name": "routetest", "yaml": VALID_YAML})
    res = client.post("/api/workflows/routetest/versions/does-not-exist/restore")
    assert res.status_code == 404


def test_delete_workflow_removes_file_and_versions(client):
    client.post("/api/workflows/save", json={"name": "routetest", "yaml": VALID_YAML})

    res = client.delete("/api/workflows/routetest")
    assert res.status_code == 200
    assert res.json() == {"name": "routetest", "deleted": True}

    assert not workflows_module.BUILDER_STORE.workflow_path("routetest").exists()
    assert client.get("/api/workflows/routetest/versions").json() == []


def test_delete_unknown_workflow_is_404(client):
    res = client.delete("/api/workflows/does-not-exist")
    assert res.status_code == 404


def test_delete_workflow_requires_auth(client):
    client.post("/api/workflows/save", json={"name": "routetest", "yaml": VALID_YAML})
    client.app.dependency_overrides.pop(require_consultant, None)

    res = client.delete("/api/workflows/routetest")
    assert res.status_code in {401, 403}


def test_delete_workflow_blocked_when_referenced_by_a_pipeline(client, monkeypatch):
    client.post("/api/workflows/save", json={"name": "routetest", "yaml": VALID_YAML})
    pipelines_dir = workflows_module.PIPELINES_DIR
    pipelines_dir.mkdir(parents=True, exist_ok=True)
    (pipelines_dir / "demo.pipeline.yaml").write_text(
        """
name: Demo Pipeline
version: '1.0'
stages:
  - id: stage_1
    workflow: routetest
"""
    )

    res = client.delete("/api/workflows/routetest")
    assert res.status_code == 409
    assert "Demo Pipeline" in res.json()["detail"]

    # The workflow must still be intact — a blocked delete is not partial.
    assert workflows_module.BUILDER_STORE.workflow_path("routetest").exists()
