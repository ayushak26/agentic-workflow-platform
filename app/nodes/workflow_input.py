"""WorkflowInputAgent — the entry primitive.

Information entering the workflow: a pasted message, an API payload, an uploaded
document reference, an email, an upstream workflow's output. Technically the
runtime already puts all of it in ``state["inputs"]``, so this node is not doing
the plumbing — it is doing three things the plumbing cannot:

1.  It gives the canvas a visible starting point, so the graph reads as the
    business process (§16) rather than beginning mid-air at an AI step.
2.  It declares the *shape* of what arrives, using the same visual field schema
    as everything else — which makes the mapping picker, the rule editor and
    preflight typed from the very first node instead of from the first AI step.
3.  It normalises the source. A `message` field is `{{inputs.message}}` whether
    the run was started from the Builder, an API call, or a prior workflow.
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.runtime.field_schema import (
    FieldSpec,
    build_response_model,
    field_paths,
)
from app.runtime.rules import resolve_path


InputSource = Literal[
    "manual",
    "api",
    "document",
    "email",
    "previous_workflow",
]

SOURCE_LABELS: dict[str, str] = {
    "manual": "Manual input",
    "api": "API input",
    "document": "Uploaded document",
    "email": "Email",
    "previous_workflow": "Previous workflow output",
}


class InputFieldBinding(FieldSpec):
    """One declared field and where its value comes from.

    A `FieldSpec` plus the two things that make sense only for a value
    *entering* the workflow rather than one an AI step must produce: where to
    read it from, and a fallback for exercising the workflow before that
    source is wired up. Being a `FieldSpec` (rather than a hand-rolled
    parallel shape) is what gives an incoming input the exact same
    enum/list/object vocabulary — including `List<Enum>` — as everywhere
    else the platform describes a shape, with no separate authoring format
    or compiler to keep in sync.
    """

    #: Path into workflow state. Defaults to `inputs.<name>`, which is the case
    #: that needs no configuration at all.
    source: str | None = None
    example: Any = None
    # A workflow input is opt-in by default — unlike an AI-produced field,
    # nothing guarantees the caller supplied it.
    required: bool = False
    #: Human-facing text shown on the field ("Customer Question") — kept
    #: separate from `name` (the stable id downstream references, e.g.
    #: "customer_question") so renaming the label never breaks a mapping.
    #: Lives here, not on the shared FieldSpec, since an AI-produced output
    #: field has no equivalent "what a human sees on a form" concept.
    label: str = ""
    #: Shown as the form field's placeholder text (input_form Start fields only).
    placeholder: str = ""

    @model_validator(mode="before")
    @classmethod
    def _default_incomplete_legacy_shape(cls, data: Any) -> Any:
        """A binding saved before `item_type`/`fields`/`enum_values` existed
        on this model (when `type` was an unvalidated free-text string) may
        be missing the piece its type now requires. Degrade to a safe,
        always-loadable shape instead of failing to parse a workflow that
        used to open fine — the author can then complete the shape from a
        working starting point rather than a raised exception."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if data.get("type") == "list" and not data.get("item_type"):
            data["item_type"] = "string"
        elif data.get("type") == "object" and not data.get("fields"):
            data["type"] = "string"
        elif data.get("type") == "enum" and not data.get("enum_values"):
            data["type"] = "string"
        return data

    def resolved_source(self) -> str:
        return self.source or f"inputs.{self.name}"


class WorkflowInputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: InputSource = Field(default="manual", description="Where this workflow's input data comes from.")
    fields: list[InputFieldBinding] = Field(
        default_factory=list,
        description="The declared shape of what enters the workflow — every downstream step addresses these by name.",
    )
    #: Sample payload used by the Builder's Test tab and the Simulator so a
    #: workflow can be exercised before any real integration is connected.
    sample: dict[str, Any] = Field(
        default_factory=dict,
        description="Sample payload used by the Builder's Test tab and Simulator to exercise the workflow before any real integration is connected.",
    )

    def as_field_specs(self) -> list[FieldSpec]:
        """The declared shape, as the same FieldSpec rows every other node uses.

        An optional binding is nullable unless the author already said so —
        the same "may be absent, use null rather than guessing" contract
        AITaskAgent's output fields use — so a downstream reader sees a
        clean `None` rather than a field that simply never appears.
        """
        return [
            FieldSpec.model_validate({
                **binding.model_dump(exclude={"source", "example", "label", "placeholder"}),
                "nullable": binding.nullable or not binding.required,
            })
            for binding in self.fields
        ]


class WorkflowInputInput(BaseModel):
    pass


class WorkflowInputOutput(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)
    source: str = "manual"
    #: Declared-but-absent required fields. A workflow can route on this rather
    #: than failing, which is what "ask the customer for what's missing" needs.
    missing: list[str] = Field(default_factory=list)


@NodeRegistry.register
class WorkflowInputAgent(NodeType):
    type_name = "WorkflowInputAgent"
    description = (
        "Information entering the workflow: manual, API, document, email or a "
        "previous workflow's output, with a declared shape."
    )
    input_schema = WorkflowInputInput
    output_schema = WorkflowInputOutput
    config_schema = WorkflowInputConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "input"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Declares what enters the workflow and normalises it into a typed "
            "object every downstream node can address."
        ),
        "why": (
            "Gives the process a visible starting point and makes the mapping "
            "picker and rule editor typed from the first node onward."
        ),
        "receives": "Workflow inputs supplied by the caller, the Builder, or an upstream run.",
        "produces": "data.<field> for each declared field, plus the list of missing required fields.",
        "uses_ai": False,
        "external_action": False,
        "presets": [
            {
                "id": source,
                "label": label,
                "summary": f"Information arriving as {label.lower()}.",
                "config": {"source": source},
            }
            for source, label in SOURCE_LABELS.items()
        ],
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return set()

    @classmethod
    def preflight_output_fields(cls, config: dict[str, Any]) -> set[str]:
        declared = set(WorkflowInputOutput.model_fields)
        try:
            specs = WorkflowInputConfig(**config).as_field_specs()
        except Exception:
            return declared | {"data"}
        return (
            declared
            | {"data"}
            | {f"data.{item.path}" for item in field_paths(specs)}
        )

    @classmethod
    def preflight_static_output_values(cls, config: dict[str, Any]) -> dict[str, Any]:
        if not (config.get("fields") or []):
            return {"data": {}}
        return {}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = WorkflowInputConfig(**resolved_config)
        data, missing = resolve_field_bindings(cfg.fields, cfg.sample, state)
        reject_values_outside_declared_shape(cfg.as_field_specs(), data)

        return {
            "data": data,
            "source": cfg.source,
            "missing": missing,
        }


def resolve_field_bindings(
    bindings: list[InputFieldBinding],
    sample: dict[str, Any],
    state: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Resolve each declared field's source path against workflow state.

    Shared by WorkflowInputAgent and StartAgent (its successor, app/nodes/start.py)
    so both entry primitives resolve fields identically — one place to fix,
    not two copies to keep in sync.
    """
    context = dict(state)
    data: dict[str, Any] = {}
    missing: list[str] = []
    for binding in bindings:
        value = resolve_path(context, binding.resolved_source())
        if value is None and binding.name in sample:
            # The configured sample is a fallback, not an override: a real
            # value always wins. This is what lets a half-built workflow run
            # end-to-end in the Builder before its inputs are wired up.
            value = sample[binding.name]
        if value is None and binding.required:
            missing.append(binding.name)
        data[binding.name] = value
    return data, missing


def reject_values_outside_declared_shape(
    specs: list[FieldSpec], data: dict[str, Any]
) -> None:
    """Validate what actually arrived against the declared shape before any
    downstream node can read it — e.g. a `List<Enum>` field given a value
    outside its allowed set, or a plain string where a list was declared.

    Compiled the same way an AI step's output schema is (`build_response_model`):
    one compiler for "what shape is this", used on both sides of the graph,
    so the values a downstream node receives are held to the same contract
    a mapping picker or preflight already believes about them.
    """
    present = {name: value for name, value in data.items() if value is not None}
    if not present:
        return
    present_specs = [spec for spec in specs if spec.name in present]
    if not present_specs:
        return
    # Required/nullable are irrelevant here — presence and required-ness are
    # already handled above via `missing`; only the shape of what's actually
    # present is being checked.
    strict_specs = [
        spec.model_copy(update={"required": True, "nullable": False})
        for spec in present_specs
    ]
    model = build_response_model(strict_specs, model_name="WorkflowInputValues")
    try:
        model.model_validate(present)
    except ValidationError as error:
        raise ValueError(
            f"workflow input does not match its declared shape: {error}"
        ) from error
