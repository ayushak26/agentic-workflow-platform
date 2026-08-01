"""Runs one pipeline stage at a time.

Each stage executes through the exact same path a standalone workflow run
does (``app/workflow/orchestration.py``), so it gets a normal run_history
entry with normal retry-from-checkpoint support. This module only decides
*what inputs to feed that stage* and *what to do once it finishes*.

Per the platform's chosen design, a pipeline run does not hold one request
open across stages: launching (or advancing) a pipeline runs exactly one
stage to completion (or to that stage's own internal HITL pause) and
returns. Advancing to the next stage — after the human reviews this stage's
output and explicitly says to proceed — is a separate, later call.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.runtime.loader import load_workflow_from_string
from app.runtime.pipeline_loader import load_pipeline_from_string, stage_workflow_path
from app.runtime.pipeline_schema import PipelineSpec, PipelineStageSpec
from app.runtime.schema import WorkflowInputSpec, WorkflowSpec
from app.runtime.executor import run_workflow
from app.runtime.templating import resolve as resolve_template
from app.workflow.orchestration import run_and_finalize, start_new_run_record
from app.workflow.pipeline_history import (
    create_pipeline_run,
    get_pipeline_run,
    record_stage_launch,
)
from app.workflow.run_history import get_run

_MISSING = object()


class PipelineExecutionError(RuntimeError):
    """Raised for pipeline-level problems the caller must fix before running
    (e.g. calling advance on a pipeline that isn't gated) — never raised for
    an ordinary stage failure, which is recorded as a normal run outcome."""


def load_stage_workflow(stage: PipelineStageSpec) -> tuple[WorkflowSpec, str]:
    path = stage_workflow_path(stage.workflow)
    if not path.exists():
        raise PipelineExecutionError(
            f"Stage {stage.id!r} references workflow {stage.workflow!r}, "
            f"which does not exist at {path}."
        )
    yaml_text = path.read_text()
    return load_workflow_from_string(yaml_text), yaml_text


def resolve_input_source(
    name: str,
    stage: PipelineStageSpec,
    pipeline_input_names: set[str],
    prior_stage_node_ids: list[tuple[str, set[str]]],
) -> tuple[str, str | None]:
    """Where would this stage input come from? Used by both preflight (to
    validate resolvability) and execution (to know which branch to take).

    Returns ``(source_kind, source_stage_id)`` where ``source_kind`` is one
    of ``explicit_mapping``, ``pipeline_input``, ``auto_matched``,
    ``unresolved``. ``source_stage_id`` is only set for ``auto_matched``.
    """
    if name in stage.inputs:
        return "explicit_mapping", None
    if name in pipeline_input_names:
        return "pipeline_input", None
    for stage_id, node_ids in reversed(prior_stage_node_ids):
        if name in node_ids:
            return "auto_matched", stage_id
    return "unresolved", None


def _coerce_for_target(value: Any, input_spec: WorkflowInputSpec) -> Any:
    """Adapt an auto-matched or mapped value to the shape a target input
    expects, mirroring the same heuristic the manual "copy run as workflow
    inputs" UI feature applies:

    - ``json`` inputs want the structured value itself. A TransformAgent's
      ``{raw, parsed}`` envelope is unwrapped to ``parsed`` automatically —
      these workflows already document that convention (e.g. an input
      described as "proposal_blueprint.parsed - the locked single source of
      truth").
    - ``text`` inputs want a string; anything else is JSON-encoded rather
      than relying on Python's ``str()`` repr (single-quoted, not valid
      JSON), which is friendlier for a downstream prompt to read.
    """
    if value is None:
        return None
    if input_spec.type == "json":
        if (
            isinstance(value, dict)
            and "parsed" in value
            and "raw" in value
        ):
            return value["parsed"]
        return value
    if input_spec.type == "text" and not isinstance(value, str):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return value


def materialize_stage_inputs(
    stage: PipelineStageSpec,
    stage_spec: WorkflowSpec,
    pipeline_inputs: dict[str, Any],
    stage_outputs_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compute the real input values for one stage, right before running it.

    ``stage_outputs_by_id`` is ``{stage_id: {node_id: output}}`` for every
    earlier stage in this pipeline run — the same shape
    ``app.runtime.templating.resolve`` already knows how to walk via
    ``{{ stage_id.node_id.field }}`` (it's identical to how a single
    workflow's own ``{{ node_id.field }}`` sugar resolves against
    ``node_outputs``), so explicit mappings reuse that function unchanged.
    """
    resolved: dict[str, Any] = {}
    template_state = {"inputs": pipeline_inputs, "node_outputs": stage_outputs_by_id}
    for name, input_spec in stage_spec.inputs.items():
        if name in stage.inputs:
            value = resolve_template(stage.inputs[name], template_state)
        elif name in pipeline_inputs:
            value = pipeline_inputs[name]
        else:
            value = _MISSING
            for source_stage in reversed(list(stage_outputs_by_id)):
                node_outputs = stage_outputs_by_id[source_stage]
                if name in node_outputs:
                    value = node_outputs[name]
                    break
            if value is _MISSING:
                # Preflight already blocks a required input with no source
                # before any stage runs — an unresolved name reaching here is
                # always optional. It still needs a key in `resolved`,
                # though: a stage workflow referencing {{inputs.<name>}}
                # would otherwise hit the same "not resolvable" crash a
                # never-supplied optional workflow input does (see
                # app.workflow.file_inputs.validate_workflow_inputs).
                value = None
        resolved[name] = _coerce_for_target(value, input_spec)
    return resolved


def _pending_stage_records(spec: PipelineSpec) -> list[dict[str, Any]]:
    return [
        {"id": stage.id, "workflow": stage.workflow, "run_id": None, "status": "pending", "error": None}
        for stage in spec.stages
    ]


async def _collect_prior_stage_outputs(
    db, session: str, pipeline_doc: dict[str, Any], upto_index: int,
) -> dict[str, dict[str, Any]]:
    """Gather every completed prior stage's node outputs, by stage id."""
    outputs_by_id: dict[str, dict[str, Any]] = {}
    for stage_record in pipeline_doc["stages"][:upto_index]:
        run_id = stage_record.get("run_id")
        if not run_id:
            continue
        stage_run = await get_run(db, session, run_id)
        outputs_by_id[stage_record["id"]] = (stage_run or {}).get("outputs") or {}
    return outputs_by_id


async def _run_stage(
    *,
    pipeline_spec: PipelineSpec,
    pipeline_run_id: str,
    stage_index: int,
    session: str,
    services: dict[str, Any],
    pipeline_inputs: dict[str, Any],
    stage_outputs_by_id: dict[str, dict[str, Any]],
    collection_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    db = services.get("audit_db")
    stage = pipeline_spec.stages[stage_index]
    stage_spec, stage_yaml = load_stage_workflow(stage)
    stage_inputs = materialize_stage_inputs(
        stage, stage_spec, pipeline_inputs, stage_outputs_by_id,
    )

    run_id = run_id or str(uuid.uuid4())
    await record_stage_launch(
        db,
        pipeline_run_id=pipeline_run_id,
        session_id=session,
        stage_index=stage_index,
        run_id=run_id,
    )
    await start_new_run_record(
        db,
        run_id=run_id,
        session=session,
        spec=stage_spec,
        workflow_yaml=stage_yaml,
        inputs=stage_inputs,
        collection_id=collection_id,
    )
    stage_result = await run_and_finalize(
        run_workflow(
            stage_spec,
            stage_inputs,
            session,
            collection_id=collection_id,
            services=services,
            run_id=run_id,
        ),
        db=db,
        run_id=run_id,
        session=session,
    )
    # run_and_finalize already reconciled pipeline_runs (it's the shared
    # finalize path every run goes through) — re-read so the response
    # reflects the pipeline's post-stage state, not its pre-stage state.
    pipeline_doc = await get_pipeline_run(db, session, pipeline_run_id)
    return {
        "pipeline_run_id": pipeline_run_id,
        "stage_id": stage.id,
        "stage_run_id": run_id,
        "stage_result": stage_result,
        "pipeline": pipeline_doc,
    }


async def run_pipeline(
    *,
    pipeline_spec: PipelineSpec,
    pipeline_yaml: str,
    pipeline_run_id: str | None,
    pipeline_inputs: dict[str, Any],
    session: str,
    services: dict[str, Any],
    collection_id: str = "default",
    stage_run_id: str | None = None,
) -> dict[str, Any]:
    """Start a new pipeline run: create its record and run stage 0."""

    pipeline_run_id = pipeline_run_id or str(uuid.uuid4())
    db = services.get("audit_db")
    await create_pipeline_run(
        db,
        pipeline_run_id=pipeline_run_id,
        session_id=session,
        pipeline_name=pipeline_spec.name,
        pipeline_yaml=pipeline_yaml,
        pipeline_inputs=pipeline_inputs,
        stages=_pending_stage_records(pipeline_spec),
    )
    return await _run_stage(
        pipeline_spec=pipeline_spec,
        pipeline_run_id=pipeline_run_id,
        stage_index=0,
        session=session,
        services=services,
        pipeline_inputs=pipeline_inputs,
        stage_outputs_by_id={},
        collection_id=collection_id,
        run_id=stage_run_id,
    )


async def advance_pipeline(
    *,
    pipeline_run_id: str,
    session: str,
    services: dict[str, Any],
    collection_id: str = "default",
    stage_run_id: str | None = None,
) -> dict[str, Any]:
    """Run the next stage of a pipeline that is gated awaiting approval."""

    db = services.get("audit_db")
    pipeline_doc = await get_pipeline_run(db, session, pipeline_run_id)
    if pipeline_doc is None:
        raise PipelineExecutionError(f"Pipeline run {pipeline_run_id!r} not found.")
    if pipeline_doc["status"] != "gated":
        raise PipelineExecutionError(
            f"Pipeline run {pipeline_run_id!r} is {pipeline_doc['status']!r}, "
            "not gated — nothing to advance."
        )

    pipeline_spec = load_pipeline_from_string(pipeline_doc["pipeline_yaml"])
    next_index = pipeline_doc["current_stage_index"] + 1
    if next_index >= len(pipeline_spec.stages):
        raise PipelineExecutionError(
            f"Pipeline run {pipeline_run_id!r} has no further stages."
        )

    stage_outputs_by_id = await _collect_prior_stage_outputs(
        db, session, pipeline_doc, next_index,
    )
    return await _run_stage(
        pipeline_spec=pipeline_spec,
        pipeline_run_id=pipeline_run_id,
        stage_index=next_index,
        session=session,
        services=services,
        pipeline_inputs=pipeline_doc.get("pipeline_inputs") or {},
        stage_outputs_by_id=stage_outputs_by_id,
        collection_id=collection_id,
        run_id=stage_run_id,
    )
