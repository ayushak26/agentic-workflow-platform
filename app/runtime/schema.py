from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.catalog import MODEL_NAMES
from app.llm.model_catalog import AUTO_MODEL


DEFAULT_LLM_MODELS = list(MODEL_NAMES)


FILE_INPUT_CATEGORIES = [
    "pdf",
    "document",
    "markdown",
    "presentation",
    "spreadsheet",
    "code",
    "image",
]


class WorkflowFileRef(BaseModel):
    """Stable object-storage reference passed through workflow state.

    Uploaded bytes never enter LangGraph state, run history, or retry
    checkpoints. Nodes receive this small reference and fetch the object only
    when they need it.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["workflow_file"] = "workflow_file"
    file_id: str
    name: str
    extension: str
    category: str
    content_type: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    minio_key: str
    parseable_text: bool = False


class WorkflowInputSpec(BaseModel):
    type: Literal["file", "text", "json"]
    description: str | None = None
    required: bool = False
    multiple: bool = False
    accept: list[str] = Field(
        default_factory=lambda: list(FILE_INPUT_CATEGORIES)
    )
    max_files: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def validate_file_options(self) -> "WorkflowInputSpec":
        if self.type != "file":
            return self
        if not self.accept:
            raise ValueError("file inputs must accept at least one file category")
        if not self.multiple and self.max_files not in (None, 1):
            raise ValueError(
                "max_files must be 1 or omitted when multiple is false"
            )
        return self

    def effective_max_files(self, platform_limit: int) -> int:
        if not self.multiple:
            return 1
        return min(self.max_files or platform_limit, platform_limit)

class ModelRoutingPolicy(BaseModel):
    """Automatic-selection policy consulted by the deterministic ModelRouter
    when a node's model is the AUTO_MODEL sentinel. Accepts a plain dict in
    YAML (Pydantic coerces) and exposes typed attributes to callers."""

    model_config = ConfigDict(extra="forbid")

    accuracy_priority: Literal["maximum", "balanced", "economy"] = "balanced"
    max_estimated_cost_usd: float | None = Field(default=None, ge=0)
    prefer_low_latency: bool = False
    quality_scores: dict[str, float] | None = None


class GuidedStageSpec(BaseModel):
    """Optional business-language grouping used by Guided Run.

    These fields never change graph execution.  They are presentation
    metadata carried with the workflow so a saved run remains understandable
    without reconstructing labels from implementation-oriented node ids.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    purpose: str = ""
    node_ids: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    expected_output: str | None = None
    visibility: Literal["standard", "summary", "advanced"] = "standard"
    weight: float = Field(default=1.0, ge=0)
    may_ask_questions: bool = False
    may_require_approval: bool = False


class WorkflowExperienceSpec(BaseModel):
    """Workflow-level settings for the non-technical runtime surface."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    goal: str | None = None
    stages: list[GuidedStageSpec] = Field(default_factory=list)


class NodeExperienceSpec(BaseModel):
    """Optional user-facing copy and handoff metadata for one node."""

    model_config = ConfigDict(extra="forbid")

    stage_id: str | None = None
    display_name: str | None = None
    purpose: str | None = None
    contribution: str | None = None
    expected_output: str | None = None
    success_condition: str | None = None
    quality_checks: list[str] = Field(default_factory=list)
    failure_message: str | None = None
    recovery_actions: list[str] = Field(default_factory=list)
    visibility: Literal["standard", "summary", "advanced"] = "standard"
    receiving_steps: list[str] = Field(default_factory=list)
    handoff_fields: list[str] = Field(default_factory=list)
    agent_role: str | None = None
    show_agent_role: bool = False


class LibraryDurationRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_minutes: int | None = Field(default=None, ge=0)
    maximum_minutes: int | None = Field(default=None, ge=0)


class LibraryHumanReviews(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=0, ge=0)
    labels: list[str] = Field(default_factory=list)


class LibraryEvidencePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drafting_requires_verified_evidence: bool | None = None
    deep_research_is_context_only: bool | None = None


class LibraryMetadataSpec(BaseModel):
    """Optional catalog/presentation metadata for the Workflow Library.

    Purely descriptive — never consulted by the compiler, preflight graph
    checks, or executor. Every field is optional so existing workflow YAML
    remains valid; the Library frontend derives honest fallbacks (title from
    name, "not yet provided" summaries, "unknown" duration) for workflows
    that don't declare it, rather than guessing evidence or approval facts.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    summary: str | None = None
    purpose: list[str] = Field(default_factory=list)
    suitable_for: list[str] = Field(default_factory=list)
    not_suitable_for: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    input_types: list[str] = Field(default_factory=list)
    typical_duration: LibraryDurationRange | None = None
    human_reviews: LibraryHumanReviews | None = None
    evidence_policy: LibraryEvidencePolicy | None = None
    visibility_status: Literal[
        "approved", "draft", "in_review", "deprecated", "archived",
    ] = "draft"
    owner_team: str | None = None


class NodeSpec(BaseModel):
    """One node instance in a workflow.

    `selected_model` is a Builder-level override. The runtime applies it to the
    node's validated config at compile time, so changing the dropdown changes
    execution rather than only changing the saved YAML.

    `model_routing` is an optional automatic-selection policy. It is only
    consulted when `selected_model` (or the node's config model) is the
    AUTO_MODEL sentinel; the deterministic ModelRouter reads it to choose a
    concrete model per call without spending tokens.
    """

    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    allowed_models: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LLM_MODELS)
    )
    selected_model: str | None = None
    model_routing: ModelRoutingPolicy | None = None
    experience: NodeExperienceSpec | None = None

    @model_validator(mode="after")
    def selected_model_must_be_allowed(self) -> "NodeSpec":
        # AUTO_MODEL is a routing sentinel, not a concrete model — it is
        # resolved at call time by the ModelRouter, so it need not appear in
        # allowed_models. Any other explicit selection must be permitted.
        if (
            self.selected_model
            and self.selected_model != AUTO_MODEL
            and self.selected_model not in self.allowed_models
        ):
            raise ValueError(
                f"selected_model {self.selected_model!r} is not in allowed_models"
            )
        return self

    def effective_config(self) -> dict[str, Any]:
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
    experience: WorkflowExperienceSpec | None = None
    library: LibraryMetadataSpec | None = None
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

        variable_names = [variable.name for variable in self.static_variables]
        if len(variable_names) != len(set(variable_names)):
            raise ValueError("static variable names must be unique")

        reserved_inputs = [
            name for name in self.inputs if name.startswith("SYSTEM.")
        ]
        if reserved_inputs:
            raise ValueError(
                "workflow inputs cannot use reserved SYSTEM.* names: "
                f"{reserved_inputs}"
            )

        if self.output is not None:
            unknown_output_nodes = [
                item.node_id
                for item in self.output.nodes
                if item.node_id not in known
            ]
            if unknown_output_nodes:
                raise ValueError(
                    "output references unknown nodes: "
                    f"{unknown_output_nodes}"
                )

        if self.experience is not None:
            stage_ids = [stage.id for stage in self.experience.stages]
            if len(stage_ids) != len(set(stage_ids)):
                raise ValueError("guided experience stage ids must be unique")
            unknown_stage_nodes = [
                node_id
                for stage in self.experience.stages
                for node_id in stage.node_ids
                if node_id not in known
            ]
            if unknown_stage_nodes:
                raise ValueError(
                    "guided experience stages reference unknown nodes: "
                    f"{unknown_stage_nodes}"
                )
            known_stages = set(stage_ids)
            unknown_node_stages = [
                node.id
                for node in self.nodes
                if (
                    node.experience is not None
                    and node.experience.stage_id is not None
                    and known_stages
                    and node.experience.stage_id not in known_stages
                )
            ]
            if unknown_node_stages:
                raise ValueError(
                    "nodes reference unknown guided experience stages: "
                    f"{unknown_node_stages}"
                )
        return self