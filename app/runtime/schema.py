from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_LLM_MODELS = [
    "claude-opus-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "gpt-5.6-sol",
    "gpt-5",
    "gpt-5-mini",
]


class WorkflowInputSpec(BaseModel):
    type: str  # "file" | "text" | "json"
    description: str | None = None


class NodeSpec(BaseModel):
    """One node instance in a workflow."""

    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    allowed_models: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LLM_MODELS)
    )
    selected_model: str | None = None

    @model_validator(mode="after")
    def selected_model_must_be_allowed(self) -> "NodeSpec":
        if (
            self.selected_model
            and self.selected_model not in self.allowed_models
        ):
            raise ValueError(
                f"selected_model {self.selected_model!r} "
                "is not in allowed_models"
            )

        return self

    def effective_config(self) -> dict[str, Any]:
        """Apply the model selected through the Builder."""

        config = dict(self.config)

        if self.selected_model:
            config["model"] = self.selected_model

        return config


class StaticVariable(BaseModel):
    name: str
    type: str
    value: Any


class WorkflowOutputNode(BaseModel):
    node_id: str
    flatten: bool = True


class WorkflowOutputSpec(BaseModel):
    include_input: bool = False
    nodes: list[WorkflowOutputNode] = Field(default_factory=list)


class EdgeSpec(BaseModel):
    """Edges support simple, fan-out, and conditional routing."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str | list[str] | None = None
    condition: str | None = None
    branches: dict[str, str] | None = None


class WorkflowSpec(BaseModel):
    """The stable, use-case-neutral workflow contract."""

    name: str
    description: str = ""
    version: str = "1.0"
    use_case: str = "generic"
    inputs: dict[str, WorkflowInputSpec] = Field(default_factory=dict)
    static_variables: list[StaticVariable] = Field(default_factory=list)
    nodes: list[NodeSpec]
    edges: list[EdgeSpec] = Field(default_factory=list)
    entry: str | None = None
    exit: str | list[str] | None = None
    output: WorkflowOutputSpec | None = None

    @model_validator(mode="after")
    def validate_graph_references(self) -> "WorkflowSpec":
        node_ids = [node.id for node in self.nodes]
        if not node_ids:
            raise ValueError("workflow must contain at least one node")
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("workflow node ids must be unique")

        known = set(node_ids)
        if self.entry and self.entry not in known:
            raise ValueError(f"entry references unknown node {self.entry!r}")
        exits = (
            [self.exit]
            if isinstance(self.exit, str)
            else (self.exit or [])
        )
        unknown_exits = [node_id for node_id in exits if node_id not in known]
        if unknown_exits:
            raise ValueError(f"exit references unknown nodes: {unknown_exits}")

        for edge in self.edges:
            if edge.from_ not in known:
                raise ValueError(
                    f"edge source references unknown node {edge.from_!r}"
                )
            targets = edge.to if isinstance(edge.to, list) else [edge.to]
            for target in [item for item in targets if item]:
                if target not in known:
                    raise ValueError(
                        f"edge target references unknown node {target!r}"
                    )
            for route, target in (edge.branches or {}).items():
                if target not in known:
                    raise ValueError(
                        f"route {route!r} references unknown node {target!r}"
                    )
        return self