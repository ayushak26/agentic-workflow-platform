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

from pydantic import BaseModel, ConfigDict, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.runtime.field_schema import FieldSpec, field_paths, parse_fields
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


class InputFieldBinding(BaseModel):
    """One declared field and where its value comes from."""

    model_config = ConfigDict(extra="forbid")

    #: Field name as downstream nodes will address it: `outputs.<node>.data.<name>`.
    name: str
    #: Path into workflow state. Defaults to `inputs.<name>`, which is the case
    #: that needs no configuration at all.
    source: str | None = None
    type: str = "string"
    description: str = ""
    required: bool = False
    example: Any = None

    def resolved_source(self) -> str:
        return self.source or f"inputs.{self.name}"


class WorkflowInputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: InputSource = "manual"
    fields: list[InputFieldBinding] = Field(default_factory=list)
    #: Sample payload used by the Builder's Test tab and the Simulator so a
    #: workflow can be exercised before any real integration is connected.
    sample: dict[str, Any] = Field(default_factory=dict)

    def as_field_specs(self) -> list[FieldSpec]:
        """The declared shape, as the same FieldSpec rows every other node uses."""
        return parse_fields(
            [
                {
                    "name": binding.name,
                    "type": binding.type if binding.type in _KINDS else "string",
                    "description": binding.description,
                    "required": binding.required,
                    "nullable": not binding.required,
                }
                for binding in self.fields
            ]
        )


_KINDS = {
    "string",
    "text",
    "number",
    "integer",
    "boolean",
    "date",
    "object",
    "list",
}


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
        context = dict(state)
        data: dict[str, Any] = {}
        missing: list[str] = []

        for binding in cfg.fields:
            value = resolve_path(context, binding.resolved_source())
            if value is None and binding.name in cfg.sample:
                # The configured sample is a fallback, not an override: a real
                # value always wins. This is what lets a half-built workflow run
                # end-to-end in the Builder before its inputs are wired up.
                value = cfg.sample[binding.name]
            if value is None and binding.required:
                missing.append(binding.name)
            data[binding.name] = value

        return {
            "data": data,
            "source": cfg.source,
            "missing": missing,
        }
