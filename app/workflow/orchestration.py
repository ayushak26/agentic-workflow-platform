"""Shared execution bookkeeping for a single workflow run.

Both a fresh run (``POST /workflows/run``) and a HITL resume
(``POST /workflows/{run_id}/resume``) need the same "create the durable
record, run it, persist whatever terminal or re-paused state comes back"
shape. A pipeline stage (``app/runtime/pipeline_executor.py``) is just a third
caller of that same shape — factoring it here means a pipeline stage behaves
identically to a standalone run in Run History (same status transitions, same
retry-checkpoint behavior) with no special-casing, and it gives pipeline
reconciliation exactly one place to hook in rather than three.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable

from app.observability.logging import get_logger
from app.runtime.schema import WorkflowSpec
from app.workflow.run_history import (
    initialize_run_checkpoint,
    mark_checkpoint_status,
    upsert_run,
)

log = get_logger(__name__)


async def start_new_run_record(
    db: Any,
    *,
    run_id: str,
    session: str,
    spec: WorkflowSpec,
    workflow_yaml: str,
    inputs: dict[str, Any],
    collection_id: str,
    retry_of_run_id: str | None = None,
    attempt: int = 1,
) -> None:
    """Create the durable "running" record and checkpoint before execution.

    Mirrors what ``POST /workflows/run`` always did before this extraction —
    called once, before the first attempt to execute a workflow's graph.
    """
    if db is None:
        return
    node_types = {n.id: n.type for n in spec.nodes}
    await upsert_run(
        db,
        run_id,
        session,
        workflow_name=spec.name,
        status="running",
        inputs=inputs,
        variables={
            variable.name: variable.value
            for variable in spec.static_variables
        },
        workflow_yaml=workflow_yaml,
        started_at=time.time(),
        node_count=len(spec.nodes),
        completed_node_count=0,
        node_types=node_types,
        retry_of_run_id=retry_of_run_id,
        attempt=attempt,
    )
    await initialize_run_checkpoint(
        db,
        run_id=run_id,
        session_id=session,
        workflow_yaml=workflow_yaml,
        inputs=inputs,
        collection_id=collection_id,
        retry_of_run_id=retry_of_run_id,
    )


async def record_run_failure(
    db: Any,
    *,
    run_id: str,
    session: str,
    error: Exception | str,
) -> dict[str, Any]:
    """Persist an exception raised while executing/resuming a run.

    Deliberately returns a plain dict rather than re-raising: the two HTTP
    call sites both need a clean 200 here so the POST doesn't bubble to an
    unhandled 500, which Starlette generates outside the CORS middleware —
    that response would lack Access-Control-Allow-Origin and the browser
    would report "Failed to fetch", masking the real error.
    """
    message = str(error)
    if db is not None:
        await upsert_run(
            db,
            run_id,
            session,
            status="failed",
            ended_at=time.time(),
            error=message[:500],
        )
        await mark_checkpoint_status(
            db, run_id=run_id, session_id=session, status="failed",
        )
    await _reconcile_pipeline_stage(db, run_id, session)
    return {"status": "failed", "run_id": run_id, "error": message}


async def finalize_run_result(
    result: dict[str, Any],
    *,
    db: Any,
    run_id: str,
    session: str,
    record_rejection_reason: bool = False,
) -> dict[str, Any]:
    """Persist a terminal or re-paused outcome. Node hooks have already
    written every completed output, so this is a consistent full-state
    snapshot, not the source of truth for any individual node.

    ``record_rejection_reason`` preserves an existing behavior difference:
    resume already stored a rejection's reason in the top-level ``error``
    field; a fresh run never did. That's arguably a gap in the fresh-run
    path, but this extraction only moves code — it doesn't change behavior —
    so the difference is kept explicit rather than silently unified.
    """
    if db is not None:
        state = result.get("state", {})
        run_status = result.get("status", "completed")
        await upsert_run(
            db,
            run_id,
            session,
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
            error=result.get("reason") if record_rejection_reason else None,
        )
        await mark_checkpoint_status(
            db, run_id=run_id, session_id=session, status=run_status,
        )
    await _reconcile_pipeline_stage(db, run_id, session)
    return result


async def run_and_finalize(
    coro: Awaitable[dict[str, Any]],
    *,
    db: Any,
    run_id: str,
    session: str,
    record_rejection_reason: bool = False,
) -> dict[str, Any]:
    """Await one execution coroutine and persist its outcome end to end.

    ``coro`` is either ``run_workflow(...)`` (a fresh run) or
    ``resume_workflow_durable(...)`` (continuing a paused one) — both return
    the same ``{"status": ..., "state": ...}`` shape, so the bookkeeping here
    doesn't need to know which one it was given. Use ``record_run_failure``
    and ``finalize_run_result`` directly instead when something (like an
    audit event) must happen between the await and the persistence.
    """
    try:
        result = await coro
    except Exception as e:
        return await record_run_failure(db, run_id=run_id, session=session, error=e)
    return await finalize_run_result(
        result,
        db=db,
        run_id=run_id,
        session=session,
        record_rejection_reason=record_rejection_reason,
    )


async def _reconcile_pipeline_stage(db: Any, run_id: str, session: str) -> None:
    """Best-effort: if this run_id is a pipeline stage, sync the pipeline.

    Never allowed to break a plain workflow run — every normal run pays one
    extra (cheap, indexed) Mongo lookup for this, and the overwhelming
    majority won't belong to any pipeline.
    """
    if db is None:
        return
    try:
        from app.workflow.pipeline_history import reconcile_stage_completion

        await reconcile_stage_completion(db, run_id=run_id, session_id=session)
    except Exception as exc:
        log.error(
            "pipeline_stage_reconcile_failed",
            error=str(exc),
            run_id=run_id,
        )
