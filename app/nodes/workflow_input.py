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

import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.runtime.field_schema import (
    FieldSpec,
    build_response_model,
    field_paths,
)
from app.runtime.rules import ConditionGroup, evaluate_group, resolve_path

# Deliberately permissive — practical validation, not strict RFC compliance
# (§4 phone/email/URL of the Start-input spec). A business form should reject
# "not an email at all", not quibble over edge cases a real customer's address
# might legitimately hit.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^https?://\S+\.\S+", re.IGNORECASE)
_PHONE_RE = re.compile(r"^[+\d][\d\s().-]{5,}$")


def _friendly_number(value: float) -> str:
    """"1.0" reads as a technical artifact of a float field; a business-
    friendly error message says "1", not "1.0"."""
    return str(int(value)) if float(value).is_integer() else str(value)

#: Attributes that exist only on InputFieldBinding (source/example/label/
#: placeholder plus every form-authoring extension) and must never reach
#: FieldSpec.model_validate — that model is extra="forbid" and shared with
#: TransformAgent's structured output. Shared by app/nodes/workflow_input.py's
#: own as_field_specs() and app/nodes/start.py's _as_field_specs() so the two
#: exclusion lists can't drift apart.
FORM_ONLY_ATTRS: frozenset[str] = frozenset({
    "source", "example", "label", "placeholder",
    "kind", "section_title", "format", "widget", "preset",
    "option_labels", "units", "display",
    "min_length", "max_length", "pattern",
    "visible_when", "required_when",
})


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

    # ---- Form-authoring extensions (Start/WorkflowInputAgent only — see
    # app/runtime/field_schema.py's own docstring on why these never touch the
    # shared FieldSpec: currency/address/lookup-shaped values, form widgets,
    # and form-only validation have no equivalent for an AI-produced output
    # field, and adding them there would widen that shared surface for
    # TransformAgent too). All optional/None-defaulting — fully backward
    # compatible with every already-saved Start/WorkflowInputAgent field. ----

    #: "field" (default, produces a data.<name> value), "info" (a heading +
    #: description shown between fields, produces no value), or "readonly"
    #: (resolved normally but rendered non-editable in the form).
    kind: Literal["field", "info", "readonly"] = "field"
    #: Renders a section heading + divider immediately before this field.
    section_title: str | None = None

    #: A widget/validation hint over an existing FieldSpec type — never
    #: changes what's stored, only how it's rendered and validated (email/url/
    #: phone over `string`; percentage over `number`, default range 0-100).
    format: Literal[
        "email", "phone", "url", "currency", "percentage", "date", "time", "datetime",
    ] | None = None
    #: Which control renders an enum/boolean field (dropdown is the default
    #: for enum, checkbox for boolean, so this is only needed to pick a
    #: non-default widget — radio/searchable_dropdown/toggle/multi_select).
    widget: Literal[
        "dropdown", "searchable_dropdown", "radio", "multi_select", "checkbox", "toggle",
    ] | None = None
    #: Tags a field as one of the compound object presets (currency,
    #: number_unit, date_range, duration, address) or the country preset —
    #: the Builder auto-generates the matching `fields`/`enum_values` shape
    #: when a preset is picked, this just tells the renderer/preflight what
    #: it's looking at.
    preset: Literal[
        "currency", "number_unit", "date_range", "duration", "address", "country",
    ] | None = None

    #: value -> display label for an enum/list-of-enum field (§6's "store the
    #: stable value, display the label") — the stored/validated value stays
    #: FieldSpec.enum_values (a flat list of stable strings); this is a purely
    #: presentational overlay, so it never touches the shared enum machinery
    #: any other consumer (TransformAgent, the rule editor) already relies on.
    option_labels: dict[str, str] | None = None
    #: Allowed units for a number_unit/duration preset, or allowed currency
    #: codes for a currency preset.
    units: list[str] | None = None
    #: How a list-of-object (repeating group) field renders: a compact table
    #: (Line Items) when every child is scalar, or stacked cards otherwise —
    #: an explicit value always wins over that default.
    display: Literal["table", "cards"] | None = None

    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None

    #: Reused verbatim from app/runtime/rules.py (the same engine behind
    #: Decision/Router rules) — evaluated against this Start node's own
    #: currently-resolved field values, not workflow graph state. A field
    #: whose visible_when is false has its value dropped and is excluded from
    #: the required check entirely (§16). A field whose required_when is true
    #: is added to `missing` if empty, on top of (not instead of) `required`
    #: (§17). Conditions may only reference an earlier-declared field in the
    #: same list — enforced by preflight and by the Builder only ever
    #: offering earlier fields as pickable — which makes a circular
    #: dependency structurally impossible rather than something to detect.
    visible_when: ConditionGroup | None = None
    required_when: ConditionGroup | None = None

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
                **binding.model_dump(exclude=FORM_ONLY_ATTRS),
                "nullable": binding.nullable or not binding.required,
            })
            for binding in self.fields
            if binding.kind != "info"
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
        data, missing = apply_conditional_fields(cfg.fields, data, missing)
        validate_field_constraints(cfg.fields, data)
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
        if binding.kind == "info":
            # A heading + body text between real fields — never has a source,
            # never contributes a value.
            continue
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


def apply_conditional_fields(
    bindings: list[InputFieldBinding],
    data: dict[str, Any],
    missing: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """Evaluate visible_when/required_when against this form's OWN
    already-resolved field values — never workflow graph state, so this
    reuses app/runtime/rules.py's evaluate_group with zero new evaluation
    logic, just a context shaped so a bare field name resolves (the same
    "name matches a node_outputs key" shorthand resolve_path already
    supports elsewhere).

    A hidden field (visible_when false) has its value dropped and is
    excluded from `missing` entirely — it was never shown, so it cannot have
    been required. A required_when field is added to `missing` when its
    condition holds and it has no value, on top of whatever `required`
    already contributed.
    """
    if not any(binding.visible_when or binding.required_when for binding in bindings):
        return data, missing

    context = {"node_outputs": data}
    result_data = dict(data)
    result_missing = list(missing)
    for binding in bindings:
        if binding.kind == "info":
            continue
        visible = True
        if binding.visible_when is not None:
            visible = evaluate_group(binding.visible_when, context).matched
        if not visible:
            result_data.pop(binding.name, None)
            if binding.name in result_missing:
                result_missing.remove(binding.name)
            continue
        if binding.required_when is not None:
            required = evaluate_group(binding.required_when, context).matched
            if (
                required
                and result_data.get(binding.name) is None
                and binding.name not in result_missing
            ):
                result_missing.append(binding.name)
    return result_data, result_missing


def validate_field_constraints(
    bindings: list[InputFieldBinding], data: dict[str, Any]
) -> None:
    """Business-friendly validation beyond shape (§20/21 — never a raw
    Pydantic path like VALIDATION_SCHEMA_FIELD_002): text length/pattern,
    email/url/phone format, percentage range, and the semantic checks a
    compound preset's own object shape can't express on its own (date_range
    ordering, an allowed unit/currency list)."""
    for binding in bindings:
        if binding.kind == "info":
            continue
        value = data.get(binding.name)
        if value is None:
            continue
        label = binding.label or binding.name

        if binding.type in ("string", "text") and isinstance(value, str):
            if binding.min_length is not None and len(value) < binding.min_length:
                raise ValueError(
                    f"{label} must be at least {binding.min_length} characters."
                )
            if binding.max_length is not None and len(value) > binding.max_length:
                raise ValueError(
                    f"{label} must be at most {binding.max_length} characters."
                )
            if binding.pattern and not re.match(binding.pattern, value):
                raise ValueError(f"{label} is not in the expected format.")
            if binding.format == "email" and not _EMAIL_RE.match(value):
                raise ValueError(f"Please enter a valid email address for {label}.")
            if binding.format == "url" and not _URL_RE.match(value):
                raise ValueError(f"Please enter a valid website address for {label}.")
            if binding.format == "phone" and not _PHONE_RE.match(value):
                raise ValueError(f"Please enter a valid phone number for {label}.")

        if (
            binding.format == "percentage"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            lower = binding.minimum if binding.minimum is not None else 0
            upper = binding.maximum if binding.maximum is not None else 100
            if not (lower <= value <= upper):
                raise ValueError(
                    f"{label} must be between {_friendly_number(lower)} and "
                    f"{_friendly_number(upper)}."
                )

        if binding.preset == "date_range" and isinstance(value, dict):
            start, end = value.get("start"), value.get("end")
            if start and end and str(end) < str(start):
                raise ValueError(
                    f"{label}: the end date must be on or after the start date."
                )

        if binding.preset in ("number_unit", "duration") and isinstance(value, dict):
            unit = value.get("unit")
            if binding.units and unit is not None and unit not in binding.units:
                raise ValueError(f"{label}: {unit!r} is not an allowed unit.")

        if binding.preset == "currency" and isinstance(value, dict):
            currency = value.get("currency")
            if binding.units and currency is not None and currency not in binding.units:
                raise ValueError(f"{label}: {currency!r} is not an allowed currency.")


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
