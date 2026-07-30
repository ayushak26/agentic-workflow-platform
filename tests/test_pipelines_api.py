from __future__ import annotations

from types import SimpleNamespace

import app.nodes  # noqa: F401
import pytest
from fastapi import HTTPException

from app.api.pipelines import (
    AdvancePipelineRequest,
    RunPipelineRequest,
    advance,
    run,
    validate_pipeline,
    ValidatePipelineRequest,
)
from app.security.dependencies import CurrentUser
from app.security.rbac import Role

from .fake_mongo import InMemoryDB

STAGE_ONE_YAML = """
name: pipeline_stage_one
version: "1.0"
nodes:
  - id: greeting
    type: Literal
    config:
      value: {text: "hello"}
entry: greeting
exit: greeting
"""

PIPELINE_YAML = """
name: single_stage_pipeline
stages:
  - id: evidence
    workflow: stage_one
"""

USER = CurrentUser(username="user@example.com", role=Role.CONSULTANT, session_id=None)


def _request(db):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(services={"audit_db": db})),
    )


@pytest.fixture
def workflows_dir(tmp_path, monkeypatch):
    (tmp_path / "stage_one.yaml").write_text(STAGE_ONE_YAML)
    monkeypatch.setattr("app.runtime.pipeline_loader.WORKFLOWS_DIR", tmp_path)
    return tmp_path


def test_validate_pipeline_reports_missing_stage_workflow():
    report = validate_pipeline(
        ValidatePipelineRequest(pipeline_yaml="name: p\nstages:\n  - id: a\n    workflow: nope\n")
    )
    assert report["valid"] is False


@pytest.mark.asyncio
async def test_run_endpoint_blocks_invalid_pipeline_with_422():
    db = InMemoryDB()
    req = RunPipelineRequest(pipeline_yaml="name: p\nstages:\n  - id: a\n    workflow: nope\n")
    with pytest.raises(HTTPException) as exc_info:
        await run(req, _request(db), USER)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_run_endpoint_launches_first_stage(workflows_dir):
    db = InMemoryDB()
    req = RunPipelineRequest(pipeline_yaml=PIPELINE_YAML)
    result = await run(req, _request(db), USER)
    assert result["stage_id"] == "evidence"
    assert result["stage_result"]["status"] == "completed"
    assert result["pipeline"]["status"] == "completed"  # only one stage


@pytest.mark.asyncio
async def test_advance_on_ungated_pipeline_returns_409(workflows_dir):
    db = InMemoryDB()
    req = RunPipelineRequest(pipeline_yaml=PIPELINE_YAML)
    launched = await run(req, _request(db), USER)
    pipeline_run_id = launched["pipeline_run_id"]

    with pytest.raises(HTTPException) as exc_info:
        await advance(pipeline_run_id, AdvancePipelineRequest(), _request(db), USER)
    assert exc_info.value.status_code == 409
