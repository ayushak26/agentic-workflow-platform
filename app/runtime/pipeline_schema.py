"""A Pipeline chains saved workflows so one workflow's node outputs become
another workflow's inputs automatically, without hand-copying JSON between
runs (see ``app/workflow/pipeline_history.py`` for the run-time record and
``app/runtime/pipeline_executor.py`` for how a stage's inputs are resolved).

Each stage references an existing workflow by name (the same name used by
``GET /workflows/by-name/{name}``). A stage's declared inputs are resolved in
this order, computed fresh for every stage right before it runs:

1. An explicit mapping in this stage's own ``inputs:`` block — a template
   expression using the same ``{{ }}`` syntax workflows already use, e.g.
   ``"{{ evidence.proposal_blueprint.parsed }}"`` or ``"{{ inputs.metadata }}"``.
2. A pipeline-level input of the same name (declared under this spec's own
   ``inputs:`` and supplied once when the pipeline is launched).
3. A node of the same name in any earlier stage's output (most recent stage
   wins on a name collision) — this is what lets stages line up with zero
   configuration when they're already named consistently, as these Horizon
   Part B workflows are today.

Anything left unresolved for a *required* input is a preflight error, not a
runtime surprise.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.runtime.schema import WorkflowInputSpec

RESERVED_STAGE_IDS = {"inputs", "outputs", "variables"}


class PipelineStageSpec(BaseModel):
    id: str
    workflow: str
    description: str | None = None
    # Explicit input-name -> template-expression overrides. Only needed when
    # the auto-match-by-name convention doesn't apply (renamed input, a
    # specific field, or a literal constant).
    inputs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def id_is_not_reserved(self) -> "PipelineStageSpec":
        if self.id in RESERVED_STAGE_IDS:
            raise ValueError(
                f"stage id {self.id!r} is reserved (clashes with the "
                "{{inputs.*}} / {{outputs.*}} template roots)"
            )
        return self


class PipelineSpec(BaseModel):
    """The stable, use-case-neutral pipeline contract."""

    name: str
    description: str = ""
    version: str = "1.0"
    inputs: dict[str, WorkflowInputSpec] = Field(default_factory=dict)
    stages: list[PipelineStageSpec]

    @model_validator(mode="after")
    def validate_stages(self) -> "PipelineSpec":
        if not self.stages:
            raise ValueError("pipeline must contain at least one stage")
        stage_ids = [stage.id for stage in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("pipeline stage ids must be unique")
        return self


class PipelineStageResult(BaseModel):
    """One stage's linkage to its underlying workflow run, as tracked by a
    pipeline run (see ``app/workflow/pipeline_history.py``)."""

    id: str
    workflow: str
    run_id: str | None = None
    status: str = "pending"
    error: str | None = None


class PipelineRunState(BaseModel):
    """In-memory shape of one pipeline run, mirrored in Mongo."""

    pipeline_run_id: str
    session_id: str
    pipeline_name: str
    pipeline_yaml: str
    pipeline_inputs: dict[str, Any] = Field(default_factory=dict)
    status: str = "running"
    current_stage_index: int = 0
    stages: list[PipelineStageResult] = Field(default_factory=list)
