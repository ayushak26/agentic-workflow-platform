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

import asyncio
import contextlib
import time
from typing import Any, Awaitable

from app.observability.logging import get_logger
from app.runtime.coordination import RedisLease
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
    pipeline_run_id: str | None = None,
    pipeline_name: str | None = None,
    stage_id: str | None = None,
    stage_index: int | None = None,
    total_stages: int | None = None,
) -> None:
    """Create the durable "running" record and checkpoint before execution.

    Mirrors what ``POST /workflows/run`` always did before this extraction —
    called once, before the first attempt to execute a workflow's graph.
    The ``pipeline_*``/``stage_*`` params are only ever passed by a pipeline
    stage launch (``app/runtime/pipeline_executor.py``) so this run's own
    Run History entry can show which stage it belongs to without a
    separate lookup into the pipeline_runs collection.
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
        pipeline_run_id=pipeline_run_id,
        pipeline_name=pipeline_name,
        stage_id=stage_id,
        stage_index=stage_index,
        total_stages=total_stages,
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
    error: BaseException | str,
    services: dict[str, Any] | None = None,
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
    await _reconcile_subprocess_callback(
        db, run_id, session, services,
        status="failed", output=None, node_outputs={}, error=message,
    )
    return {"status": "failed", "run_id": run_id, "error": message}


async def finalize_run_result(
    result: dict[str, Any],
    *,
    db: Any,
    run_id: str,
    session: str,
    record_rejection_reason: bool = False,
    services: dict[str, Any] | None = None,
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
    run_status = result.get("status", "completed")
    state = result.get("state", {})
    if db is not None:
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
    await _reconcile_subprocess_callback(
        db, run_id, session, services,
        status=run_status,
        output=result.get("output"),
        node_outputs=state.get("node_outputs", {}),
        error=result.get("reason") if record_rejection_reason else None,
    )
    return result


async def run_and_finalize(
    coro: Awaitable[dict[str, Any]],
    *,
    db: Any,
    run_id: str,
    session: str,
    record_rejection_reason: bool = False,
    services: dict[str, Any] | None = None,
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
    except asyncio.CancelledError as e:
        # Only reaches here when something cancels the task actually running
        # the workflow (e.g. process shutdown) — a dropped HTTP request no
        # longer does this, since BackgroundRunManager detaches execution
        # from the request task. Still record it rather than leaving the run
        # stuck "running" forever, then let the cancellation keep propagating.
        await record_run_failure(
            db, run_id=run_id, session=session, error=e, services=services,
        )
        raise
    except Exception as e:
        return await record_run_failure(
            db, run_id=run_id, session=session, error=e, services=services,
        )
    return await finalize_run_result(
        result,
        db=db,
        run_id=run_id,
        session=session,
        record_rejection_reason=record_rejection_reason,
        services=services,
    )


class BackgroundRunManager:
    """Own detached run tasks with a Redis lease shared by all workers.

    A workflow run can take minutes; holding the HTTP request open for that
    long means any idle-connection timeout upstream (reverse proxy, browser,
    NAT) cancels the request task and, with it, whatever call the workflow
    was in the middle of — this is what produced a recurring
    ``asyncio.CancelledError`` inside the OpenAI client mid-run. Detaching
    execution into its own task means a dropped request no longer touches
    it; Run History (``start_new_run_record``) and the SSE event bus are how
    callers observe progress and the terminal result instead. The Redis lease
    makes a repeated launch of the same run_id a no-op on every worker, while
    the local task set is only lifecycle bookkeeping for graceful shutdown.
    """

    def __init__(self, redis: Any | None, *, lease_seconds: int = 120) -> None:
        """Initialize the BackgroundRunManager.

        Args:
            redis (Any | None): Redis client.
            lease_seconds (int): The lease seconds (optional, default 120).
        """
        self._redis = redis
        self._lease_seconds = max(30, lease_seconds)
        self._tasks: set[asyncio.Task[None]] = set()

    def launch(
        self,
        coro: Awaitable[dict[str, Any]],
        *,
        db: Any,
        run_id: str,
        session: str,
        record_rejection_reason: bool = False,
        services: dict[str, Any] | None = None,
    ) -> None:
        """Launch the result.

        Args:
            coro (Awaitable[dict[str, Any]]): The coro.
            db (Any): Mongo database handle.
            run_id (str): Workflow run identifier.
            session (str): Session scope the record belongs to.
            record_rejection_reason (bool): The record rejection reason (optional, default False).
            services (dict[str, Any] | None): Shared application services dict (optional, default None).
        """
        task = asyncio.create_task(
            self._run_owned(
                coro,
                db=db,
                run_id=run_id,
                session=session,
                record_rejection_reason=record_rejection_reason,
                services=services,
            ),
            name=f"workflow-run:{run_id}",
        )
        self._tasks.add(task)

        def _on_done(done: asyncio.Task[None]) -> None:
            """Internal helper for the on done step.

            Args:
                done (asyncio.Task[None]): The done.
            """
            self._tasks.discard(done)
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                log.error(
                    "background_run.task_failed",
                    run_id=run_id,
                    error=str(exc),
                )

        task.add_done_callback(_on_done)

    async def _run_owned(
        self,
        coro: Awaitable[dict[str, Any]],
        *,
        db: Any,
        run_id: str,
        session: str,
        record_rejection_reason: bool,
        services: dict[str, Any] | None = None,
    ) -> None:
        """Run the owned.

        Args:
            coro (Awaitable[dict[str, Any]]): The coro.
            db (Any): Mongo database handle.
            run_id (str): Workflow run identifier.
            session (str): Session scope the record belongs to.
            record_rejection_reason (bool): The record rejection reason.
            services (dict[str, Any] | None): Shared application services dict (optional, default None).
        """
        if self._redis is None:
            await run_and_finalize(
                coro,
                db=db,
                run_id=run_id,
                session=session,
                record_rejection_reason=record_rejection_reason,
                services=services,
            )
            return

        lease = RedisLease(
            self._redis,
            f"awp:run-owner:{run_id}",
            ttl_seconds=self._lease_seconds,
        )
        try:
            acquired = await lease.acquire()
        except Exception as exc:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            await record_run_failure(
                db,
                run_id=run_id,
                session=session,
                error=f"Distributed run ownership is unavailable: {exc}",
                services=services,
            )
            log.error(
                "background_run.lease_unavailable",
                run_id=run_id,
                error_type=type(exc).__name__,
            )
            return
        if not acquired:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            log.warning("background_run.already_owned", run_id=run_id)
            return

        execution = asyncio.create_task(
            run_and_finalize(
                coro,
                db=db,
                run_id=run_id,
                session=session,
                record_rejection_reason=record_rejection_reason,
                services=services,
            )
        )
        heartbeat = asyncio.create_task(lease.keep_alive())
        try:
            done, _ = await asyncio.wait(
                {execution, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if execution in done:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
                await execution
                return

            # Ownership expired or was replaced. Stop this worker before a new
            # owner can execute the same run concurrently.
            execution.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await execution
            log.error("background_run.lease_lost", run_id=run_id)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            with contextlib.suppress(Exception):
                await lease.release()

    async def close(self) -> None:
        """Cancel owned work and let run_and_finalize persist interruption."""

        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


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


async def _reconcile_subprocess_callback(
    db: Any,
    run_id: str,
    session: str,
    services: dict[str, Any] | None,
    *,
    status: str,
    output: Any,
    node_outputs: dict[str, Any],
    error: str | None,
) -> None:
    """Best-effort: if this run_id is a Subprocess node's child, deliver its
    result to the waiting parent (app.workflow.subprocess_callback).

    Never allowed to break a plain workflow run — one cheap indexed Mongo
    lookup either way, same contract as _reconcile_pipeline_stage above.
    A child still "paused" (its own HITL gate, say) has not actually
    finished, so this only fires on a genuinely terminal status.
    """
    if db is None or status not in ("completed", "rejected", "failed"):
        return
    try:
        from app.workflow.subprocess_callback import deliver_by_child_run_id

        await deliver_by_child_run_id(
            db, services or {}, run_id,
            status=status, output=output, node_outputs=node_outputs, error=error,
        )
    except Exception as exc:
        log.error(
            "subprocess_callback_reconcile_failed",
            error=str(exc),
            run_id=run_id,
        )