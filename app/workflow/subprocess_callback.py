"""Deliver a finished child run's result to its waiting Subprocess parent.

This is the one implementation of "what happens when a Subprocess's child
finishes," shared by the only two callers that should ever need it:

  - app.api.workflows' POST /workflows/subprocess-callback/{token} — a real,
    independently callable endpoint, so a child's completion can be reported
    from wherever it actually happened (this deployment's own worker
    finishing a normal run is the common case, but nothing about the
    endpoint requires that).
  - app.workflow.orchestration's finalize hook, which calls this in-process
    the moment an ordinary run's own finalize path completes — the child
    launched by SubprocessAgent is not run any differently than a run a
    person started by hand, so it finishes through that exact same path.

Both go through the same token-gated, single-use delivery below, so there is
never a second, drifting copy of "how a child's result reaches its parent."
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.observability.logging import get_logger
from app.runtime.hitl import (
    HITLResumeConflict,
    HITLResumeError,
    resume_workflow_durable,
)
from app.workflow import subprocess_launches

log = get_logger(__name__)

# A child can finish and call back before the parent's own pause has fully
# persisted: the checkpoint write and the in-process graph cache
# registration both happen slightly *after* interrupt() raises, on the same
# task a trivially fast child's completion can race against (verified: a
# single Literal->Echo child can complete and reach here before either has
# landed). A short bounded retry absorbs that ordering gap; a run_id that
# genuinely never paused still fails, just a few dozen milliseconds later.
_NOT_YET_PAUSED_RETRIES = 5
_NOT_YET_PAUSED_RETRY_DELAY_SECONDS = 0.05

# A child run's terminal status, translated into what the parent's
# SubprocessAgent.run() sees on resume (app/nodes/subprocess.py). A child
# left "paused" (its own HITL gate, say) never reaches here — see
# app.workflow.orchestration._reconcile_subprocess_callback, which only
# calls this once the child is genuinely done.
_STATUS_MAP = {"completed": "completed", "rejected": "rejected", "failed": "failed"}


def _select_result(
    launch: dict[str, Any],
    *,
    output: Any,
    node_outputs: dict[str, Any],
) -> Any:
    """Project the child's result the way the SubprocessConfig author asked
    for (result_from, recorded on the launch doc at reserve time — see
    app/nodes/subprocess.py's SubprocessConfig):

      workflow_output  the child's own declared `output:` contract (what
                        every other caller of that workflow would get back)
      node              one specific node's raw output — for a child with
                         no output contract, or a specific intermediate step
      all_outputs       every node's raw output, keyed by node id
    """
    result_from = launch.get("result_from") or "workflow_output"
    if result_from == "all_outputs":
        return node_outputs
    if result_from == "node":
        return node_outputs.get(launch.get("result_node") or "")
    return output


async def deliver_by_token(
    db: Any,
    services: dict[str, Any],
    token: str,
    *,
    status: str,
    output: Any,
    node_outputs: dict[str, Any],
    error: str | None,
) -> dict[str, Any]:
    """Deliver the by token.

    Args:
        db (Any): Mongo database handle.
        services (dict[str, Any]): Shared application services dict.
        token (str): Token value.
        status (str): Status value.
        output (Any): Node output mapping.
        node_outputs (dict[str, Any]): The node outputs.
        error (str | None): Error value or message.

    Returns:
        dict[str, Any]: The by token.
    """
    launch = await subprocess_launches.find_by_token(db, token)
    if launch is None or launch.get("status") != "pending":
        # Already delivered, expired, or the token never existed — a no-op,
        # not an error, so a retried or duplicate call is always safe.
        return {"status": "noop"}

    decision = {
        "status": _STATUS_MAP.get(status, "failed"),
        "result": (
            _select_result(launch, output=output, node_outputs=node_outputs)
            if status == "completed" else None
        ),
        "error": error,
        "child_run_id": launch["child_run_id"],
        "child_workflow": launch["child_workflow"],
    }

    # Marked delivered *before* resuming the parent, not after: resuming is
    # exactly what makes SubprocessAgent.run() re-execute from the top (see
    # its own module docstring), and that re-entry must already see
    # "delivered" — otherwise, if the parent's own step then fails on this
    # exact decision (the child failed, say), a later retry would find the
    # launch still "pending" and could attempt to launch a second child for
    # a step whose answer was, in fact, already delivered.
    delivered = await subprocess_launches.deliver(db, token=token, decision=decision)
    if not delivered:
        return {"status": "noop"}

    from app.workflow.orchestration import finalize_run_result, record_run_failure

    attempt = 0
    while True:
        try:
            result = await resume_workflow_durable(
                launch["parent_run_id"],
                decision,
                services=services,
                session_id=launch["parent_session_id"],
                actor="system:subprocess",
            )
            break
        except HITLResumeError:
            attempt += 1
            if attempt >= _NOT_YET_PAUSED_RETRIES:
                raise
            await asyncio.sleep(_NOT_YET_PAUSED_RETRY_DELAY_SECONDS)
        except HITLResumeConflict:
            # Someone/something else already resumed this exact checkpoint —
            # the caller decides what to do with that (the standalone
            # endpoint turns it into an HTTP 409; the in-process finalize
            # hook just logs it), not this function's job to paper over.
            raise
        except Exception as exc:
            # Mirrors POST /workflows/{run_id}/resume's own handling: a node
            # failing after resume — here, typically the parent's own
            # SubprocessAgent step raising because the child did not
            # complete (see SubprocessAgent._finalize) — ends the parent run
            # rather than leaving it silently stuck "paused" forever.
            return await record_run_failure(
                db,
                run_id=launch["parent_run_id"],
                session=launch["parent_session_id"],
                error=exc,
                services=services,
            )

    # resume_workflow_durable only computes the outcome — persisting it to
    # Run History is finalize_run_result's job, exactly as
    # POST /workflows/{run_id}/resume already does for a human-driven resume.
    return await finalize_run_result(
        result,
        db=db,
        run_id=launch["parent_run_id"],
        session=launch["parent_session_id"],
        services=services,
    )


async def deliver_by_child_run_id(
    db: Any,
    services: dict[str, Any],
    child_run_id: str,
    *,
    status: str,
    output: Any,
    node_outputs: dict[str, Any],
    error: str | None,
) -> None:
    """Deliver completion when the run belongs to a Subprocess launch.

    The overwhelming majority of runs are not Subprocess children, so this is
    one cheap indexed lookup and an immediate return for ordinary Workflow
    runs.
    """
    if db is None:
        return
    launch = await subprocess_launches.find_by_child_run_id(db, child_run_id)
    if launch is None:
        return
    try:
        await deliver_by_token(
            db, services, launch["token"],
            status=status, output=output, node_outputs=node_outputs, error=error,
        )
    except Exception as exc:
        log.error(
            "subprocess_callback_delivery_failed",
            error=str(exc),
            child_run_id=child_run_id,
        )
