import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string
from app.security.dependencies import CurrentUser, require_consultant
from app.security.audit import read_audit_events
from app.workflow.run_history import (
    get_retry_checkpoint,
    get_run,
    initialize_run_checkpoint,
    list_runs,
    mark_checkpoint_status,
    upsert_run,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _scope(user: CurrentUser) -> str:
    return getattr(user, "session_id", None) or user.username


@router.get("/mine")
async def my_runs(
    request: Request,
    limit: int = 50,
    user: CurrentUser = Depends(require_consultant),
):
    db = request.app.state.services.get("audit_db")
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "run store unavailable")
    try:
        runs = await list_runs(db, _scope(user), limit=limit)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"count": len(runs), "runs": runs}


@router.get("/mine/{run_id}")
async def my_run_detail(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    db = request.app.state.services.get("audit_db")
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "run store unavailable")
    scope = _scope(user)
    try:
        run = await get_run(db, scope, run_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    checkpoint = await get_retry_checkpoint(db, scope, run_id)
    run["retry_available"] = (
        run.get("status") == "failed" and checkpoint is not None
    )
    run["retryable_node_count"] = len(
        (checkpoint or {}).get("reusable_results", {})
    )
    audit = await read_audit_events(db, scope, run_id=run_id)
    return {"run": run, "audit": audit}


class RetryRunRequest(BaseModel):
    run_id: str | None = None


@router.post("/mine/{source_run_id}/retry")
async def retry_failed_run(
    source_run_id: str,
    req: RetryRunRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Create a new attempt that reuses every completed node checkpoint."""

    services = getattr(request.app.state, "services", {})
    db = services.get("audit_db")
    if db is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "run store unavailable",
        )

    scope = _scope(user)
    source = await get_run(db, scope, source_run_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if source.get("status") != "failed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only failed runs can be retried",
        )

    checkpoint = await get_retry_checkpoint(db, scope, source_run_id)
    if checkpoint is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This run has no retry checkpoint. Run it once after installing "
            "the checkpoint update.",
        )

    workflow_yaml = checkpoint.get("workflow_yaml")
    raw_inputs = checkpoint.get("inputs") or {}
    collection_id = checkpoint.get("collection_id") or "default"
    if not workflow_yaml:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The retry checkpoint does not contain workflow YAML",
        )
    try:
        spec = load_workflow_from_string(workflow_yaml)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The saved workflow can no longer be loaded: {exc}",
        ) from exc

    retry_run_id = req.run_id or str(uuid.uuid4())
    if retry_run_id == source_run_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A retry must use a new run_id",
        )
    if await get_run(db, scope, retry_run_id) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The requested retry run_id already exists",
        )

    reusable_results = checkpoint.get("reusable_results") or {}
    attempt = int(source.get("attempt") or 1) + 1
    started_at = time.time()
    node_types = {node.id: node.type for node in spec.nodes}

    await upsert_run(
        db,
        retry_run_id,
        scope,
        workflow_name=spec.name,
        status="running",
        inputs=raw_inputs,
        workflow_yaml=workflow_yaml,
        started_at=started_at,
        node_count=len(spec.nodes),
        completed_node_count=0,
        node_types=node_types,
        retry_of_run_id=source_run_id,
        attempt=attempt,
        reused_node_count=0,
        reused_nodes=[],
    )
    await initialize_run_checkpoint(
        db,
        run_id=retry_run_id,
        session_id=scope,
        workflow_yaml=workflow_yaml,
        inputs=raw_inputs,
        collection_id=collection_id,
        retry_of_run_id=source_run_id,
    )

    try:
        result = await run_workflow(
            spec,
            raw_inputs,
            scope,
            collection_id=collection_id,
            services=services,
            run_id=retry_run_id,
            reused_node_results=reusable_results,
            retry_source_run_id=source_run_id,
        )
    except Exception as exc:
        await upsert_run(
            db,
            retry_run_id,
            scope,
            status="failed",
            ended_at=time.time(),
            error=str(exc)[:500],
        )
        await mark_checkpoint_status(
            db,
            run_id=retry_run_id,
            session_id=scope,
            status="failed",
        )
        return {
            "status": "failed",
            "run_id": retry_run_id,
            "error": str(exc),
            "retry": {
                "source_run_id": source_run_id,
                "reused_node_count": len(reusable_results),
            },
        }

    state = result.get("state", {})
    run_status = result.get("status", "completed")
    await upsert_run(
        db,
        retry_run_id,
        scope,
        status=run_status,
        outputs=(
            state.get("node_outputs", {})
            if run_status != "paused"
            else None
        ),
        ended_at=(
            time.time()
            if run_status in {"completed", "rejected", "failed"}
            else None
        ),
        completed_node_count=len(state.get("node_outputs", {})),
    )
    await mark_checkpoint_status(
        db,
        run_id=retry_run_id,
        session_id=scope,
        status=run_status,
    )
    return result
