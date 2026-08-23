"""StartAgent: the canonical workflow entry point.

Successor to WorkflowInputAgent (app/nodes/workflow_input.py, kept registered
and fully functional for existing workflows, hidden from the palette for new
ones). Start gives a workflow a visually distinct beginning and unifies what
used to be two disconnected authoring surfaces — the node's own declared
fields, and the workflow-level `inputs:` block — under one place a user
configures "what does this workflow need to begin" (see
WorkflowSpec.derive_inputs_from_start in app/runtime/schema.py).

Two modes, never both at once:
- input_form: a business-friendly form — typed fields plus file uploads.
- chatbot: a conversational entry point — message + optional attachments.

Start never decides anything (no RAG, no LLM, no routing) — it only collects
and normalizes what entered the workflow. That is Start's entire job.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.nodes.workflow_input import (
    FORM_ONLY_ATTRS,
    InputFieldBinding,
    apply_conditional_fields,
    reject_values_outside_declared_shape,
    resolve_field_bindings,
    validate_field_constraints,
)
from app.runtime.field_schema import FieldSpec, field_paths
from app.runtime.rules import resolve_path
from app.runtime.schema import FILE_INPUT_CATEGORIES


class StartFileField(BaseModel):
    """A file-typed form field. Kept separate from InputFieldBinding/FieldSpec
    (whose `type` vocabulary has no file kind — see app/runtime/field_schema.py)
    so file handling stays entirely inside the existing WorkflowFileRef/
    upload-validation machinery rather than teaching the shared structured-
    output compiler about object storage."""

    name: str
    label: str
    required: bool = False
    multiple: bool = False
    accept: list[str] = Field(default_factory=lambda: list(FILE_INPUT_CATEGORIES))
    max_files: int | None = Field(default=None, ge=1, le=20)
    source: str | None = None

    def resolved_source(self) -> str:
        """Compute the resolved source.

        Returns:
            str: The source.
        """
        return self.source or f"inputs.{self.name}"


class StartConfig(BaseModel):
    """Pydantic model defining the StartConfig shape.

    Attributes:
        mode (str).
        title (str).
        description (str).
        fields (list[InputFieldBinding]).
        file_fields (list[StartFileField]).
        sample (dict[str, Any]).
        chatbot_name (str).
        welcome_message (str).
    """
    mode: str = "input_form"  # "input_form" | "chatbot"

    # input_form mode
    title: str = ""
    description: str = ""
    fields: list[InputFieldBinding] = Field(default_factory=list)
    file_fields: list[StartFileField] = Field(default_factory=list)
    sample: dict[str, Any] = Field(default_factory=dict)

    # chatbot mode
    chatbot_name: str = ""
    welcome_message: str = ""
    message_placeholder: str = "Ask a question..."
    allow_attachments: bool = True
    suggested_questions: list[str] = Field(default_factory=list)


class StartInput(BaseModel):
    """Pydantic model defining the StartInput shape."""
    pass


class StartOutput(BaseModel):
    # input_form mode
    """Pydantic model defining the StartOutput shape.

    Attributes:
        data (dict[str, Any]).
        message (str | None).
        attachments (list[dict[str, Any]]).
        missing (list[str]).
    """
    data: dict[str, Any] = Field(default_factory=dict)
    # chatbot mode
    message: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    # shared
    missing: list[str] = Field(default_factory=list)


@NodeRegistry.register
class StartAgent(NodeType):
    """Workflow node type implementing the StartAgent capability.

    Attributes:
        family (ClassVar[str]).
        execution_kind (ClassVar[str]).
        about (ClassVar[dict[str, Any]]).
    """
    type_name = "StartAgent"
    description = (
        "How this workflow begins: a business-friendly input form, or a "
        "conversational chatbot entry point."
    )
    input_schema = StartInput
    output_schema = StartOutput
    config_schema = StartConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "input"
    about: ClassVar[dict[str, Any]] = {
        "what": "Declares how information enters the workflow — a form, or a chat message.",
        "why": "Gives the workflow a visible, understandable starting point.",
        "receives": "Whatever the caller, form submission, or chat message supplies.",
        "produces": "data.<field> for each declared field (Input Form), or message/attachments (Chatbot).",
        "uses_ai": False,
        "external_action": False,
        "presets": [
            {
                "id": "input_form",
                "label": "Input Form",
                "summary": "Collect structured information before starting the workflow.",
                "config": {"mode": "input_form"},
            },
            {
                "id": "chatbot",
                "label": "Chatbot Interface",
                "summary": "Start the workflow from a conversational message.",
                "config": {"mode": "chatbot"},
            },
        ],
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return set()

    @classmethod
    def preflight_output_fields(cls, config: dict[str, Any]) -> set[str]:
        """Compute the preflight output fields.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The output fields.
        """
        declared = set(StartOutput.model_fields)
        if config.get("mode") == "chatbot":
            return declared
        try:
            cfg = StartConfig(**config)
            specs = _as_field_specs(cfg.fields)
        except Exception:
            return declared | {"data"}
        return (
            declared
            | {"data"}
            | {f"data.{item.path}" for item in field_paths(specs)}
            | {f"data.{file_field.name}" for file_field in cfg.file_fields}
        )

    @classmethod
    def preflight_static_output_values(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Compute the preflight static output values.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            dict[str, Any]: The static output values.
        """
        if config.get("mode") == "chatbot":
            return {}
        if not (config.get("fields") or []) and not (config.get("file_fields") or []):
            return {"data": {}}
        return {}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = StartConfig(**resolved_config)

        if cfg.mode == "chatbot":
            message = resolve_path(dict(state), "inputs.message")
            attachments: list[dict[str, Any]] = []
            if cfg.allow_attachments:
                raw = resolve_path(dict(state), "inputs.attachments")
                if isinstance(raw, list):
                    attachments = raw
                elif raw is not None:
                    attachments = [raw]
            missing = ["message"] if message is None else []
            return {
                "data": {},
                "message": message,
                "attachments": attachments,
                "missing": missing,
            }

        data, missing = resolve_field_bindings(cfg.fields, cfg.sample, state)
        data, missing = apply_conditional_fields(cfg.fields, data, missing)
        # Business-friendly checks first, so a percentage/email/length problem
        # surfaces with its own clear message rather than the generic
        # shape-compiler's raw Pydantic error winning the race.
        validate_field_constraints(cfg.fields, data)
        reject_values_outside_declared_shape(_as_field_specs(cfg.fields), data)

        context = dict(state)
        for file_field in cfg.file_fields:
            value = resolve_path(context, file_field.resolved_source())
            if value is None and file_field.required:
                missing.append(file_field.name)
            data[file_field.name] = value

        return {
            "data": data,
            "message": None,
            "attachments": [],
            "missing": missing,
        }


def _as_field_specs(fields: list[InputFieldBinding]) -> list[FieldSpec]:
    """Internal helper for the as field specs step.

    Args:
        fields (list[InputFieldBinding]): Field names.

    Returns:
        list[FieldSpec]: The field specs.
    """
    return [
        FieldSpec.model_validate({
            **field.model_dump(exclude=FORM_ONLY_ATTRS),
            "nullable": field.nullable or not field.required,
        })
        for field in fields
        if field.kind != "info"
    ]
