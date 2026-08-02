"""Pipelines chain saved workflows so one workflow's node outputs become
another workflow's inputs automatically. See app/runtime/pipeline_schema.py
for the file format and app/runtime/pipeline_executor.py for how a stage's
inputs get resolved and how advancing between stages works.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
import yaml

from app.api.workflows import _reserve_run_id, _scope
from app.runtime.pipeline_executor import (
    PipelineExecutionError,
    advance_pipeline,
    run_pipeline,
)
from app.runtime.pipeline_loader import PIPELINES_DIR, load_pipeline_from_string
from app.runtime.pipeline_preflight import preflight_pipeline_yaml
from app.security.dependencies import CurrentUser, require_consultant
from app.workflow.file_inputs import WorkflowFileInputError, validate_workflow_inputs
from app.workflow.pipeline_history import (
    abandon_pipeline,
    get_pipeline_run,
    list_pipeline_runs,
)

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


class ValidatePipelineRequest(BaseModel):
    pipeline_yaml: str
    inputs: dict[str, Any] | None = None


@router.post("/validate")
def validate_pipeline(req: ValidatePipelineRequest):
    """Validate a pipeline without running a stage or spending tokens."""
    report = preflight_pipeline_yaml(req.pipeline_yaml, provided_inputs=req.inputs)
    return report.model_dump(mode="json")


class RunPipelineRequest(BaseModel):
    pipeline_yaml: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    pipeline_run_id: str | None = None
    # Client-supplied id for stage 0's underlying workflow run. Lets the UI
    # open a live Cockpit (SSE) view before triggering the run, exactly like
    # POST /workflows/run's run_id — see _reserve_run_id for the reservation
    # semantics that make this safe.
    stage_run_id: str | None = None


def _preflight_http_detail(report) -> dict[str, Any]:
    return {
        "message": (
            f"Pipeline preflight found {len(report.errors)} blocking "
            "error(s). No stage was run."
        ),
        "preflight": report.model_dump(mode="json"),
    }


@router.post("/run")
async def run(
    req: RunPipelineRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    services = getattr(request.app.state, "services", {})
    session = _scope(user, req.session_id)

    try:
        spec = load_pipeline_from_string(req.pipeline_yaml)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid pipeline YAML: {e}")

    preflight = preflight_pipeline_yaml(req.pipeline_yaml, provided_inputs=req.inputs)
    if not preflight.valid:
        raise HTTPException(status_code=422, detail=_preflight_http_detail(preflight))

    try:
        validated_inputs = await validate_workflow_inputs(
            spec.inputs,
            req.inputs,
            session_id=session,
            object_store=services.get("object_store"),
        )
    except WorkflowFileInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db = services.get("audit_db")
    pipeline_run_id = req.pipeline_run_id or str(uuid.uuid4())
    if db is not None:
        existing = await db["pipeline_runs"].find_one(
            {"pipeline_run_id": pipeline_run_id},
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"pipeline_run_id already exists: {pipeline_run_id}",
            )

    stage_run_id = None
    if req.stage_run_id:
        stage_run_id = await _reserve_run_id(
            services, run_id=req.stage_run_id, session_id=session,
        )

    try:
        return await run_pipeline(
            pipeline_spec=spec,
            pipeline_yaml=req.pipeline_yaml,
            pipeline_run_id=pipeline_run_id,
            pipeline_inputs=validated_inputs,
            session=session,
            services=services,
            stage_run_id=stage_run_id,
        )
    except PipelineExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class AdvancePipelineRequest(BaseModel):
    session_id: str | None = None
    # Client-supplied id for the next stage's underlying workflow run — same
    # purpose as RunPipelineRequest.stage_run_id, for the "Continue to next
    # stage" action.
    stage_run_id: str | None = None


@router.post("/{pipeline_run_id}/advance")
async def advance(
    pipeline_run_id: str,
    req: AdvancePipelineRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    services = getattr(request.app.state, "services", {})
    session = _scope(user, req.session_id)
    stage_run_id = None
    if req.stage_run_id:
        stage_run_id = await _reserve_run_id(
            services, run_id=req.stage_run_id, session_id=session,
        )
    try:
        return await advance_pipeline(
            pipeline_run_id=pipeline_run_id,
            session=session,
            services=services,
            stage_run_id=stage_run_id,
        )
    except PipelineExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/mine")
async def my_pipeline_runs(
    request: Request,
    limit: int = 50,
    user: CurrentUser = Depends(require_consultant),
):
    services = getattr(request.app.state, "services", {})
    db = services.get("audit_db")
    if db is None:
        raise HTTPException(status_code=503, detail="run store unavailable")
    runs = await list_pipeline_runs(db, _scope(user, None), limit=limit)
    return {"count": len(runs), "runs": runs}


@router.get("/mine/{pipeline_run_id}")
async def my_pipeline_run_detail(
    pipeline_run_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    services = getattr(request.app.state, "services", {})
    db = services.get("audit_db")
    if db is None:
        raise HTTPException(status_code=503, detail="run store unavailable")
    run = await get_pipeline_run(db, _scope(user, None), pipeline_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return run


class AbandonPipelineRequest(BaseModel):
    session_id: str | None = None


@router.post("/{pipeline_run_id}/abandon")
async def abandon(
    pipeline_run_id: str,
    req: AbandonPipelineRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Manually move a stuck pipeline out of running/gated so any run that is
    one of its stages stops being permanently delete-blocked. Intended for a
    pipeline whose stage run never reached a terminal status through the
    normal completion path (e.g. it was deleted, or its process died before
    ever finalizing) — see find_active_pipeline_stage."""
    services = getattr(request.app.state, "services", {})
    db = services.get("audit_db")
    if db is None:
        raise HTTPException(status_code=503, detail="run store unavailable")
    session = _scope(user, req.session_id)
    abandoned = await abandon_pipeline(
        db, pipeline_run_id=pipeline_run_id, session_id=session,
    )
    if not abandoned:
        existing = await get_pipeline_run(db, session, pipeline_run_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        raise HTTPException(
            status_code=409,
            detail=f"Pipeline is already {existing.get('status')!r} — nothing to abandon.",
        )
    return {"pipeline_run_id": pipeline_run_id, "status": "abandoned"}


@router.get("")
def list_pipelines():
    """Library view: saved pipeline files on disk, mirroring GET /workflows."""
    out = []
    for p in sorted(PIPELINES_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text()) or {}
        except yaml.YAMLError:
            continue
        out.append({
            "name": p.stem,
            "description": data.get("description", ""),
            "version": data.get("version", "1.0"),
            "stage_count": len(data.get("stages", [])),
        })
    return out


@router.get("/by-name/{name}")
def get_pipeline(name: str):
    p = PIPELINES_DIR / f"{name}.yaml"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"pipeline '{name}' not found")
    return {"name": name, "yaml": p.read_text()}


class SavePipelineRequest(BaseModel):
    name: str
    yaml: str


@router.post("/save")
def save_pipeline(req: SavePipelineRequest):
    report = preflight_pipeline_yaml(req.yaml)
    if not report.valid:
        raise HTTPException(status_code=422, detail=_preflight_http_detail(report))

    if not req.name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="name must be alphanumeric + _ -")

    PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    (PIPELINES_DIR / f"{req.name}.yaml").write_text(req.yaml)
    return {"ok": True, "name": req.name}
