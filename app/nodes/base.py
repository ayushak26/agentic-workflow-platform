"""Base contract for every node type in the platform.

A NodeType is a typed, schema-declared, async unit of work that the runtime
can introspect, validate, render in the UI, and execute. Subclasses declare
three pydantic schemas and one async method. That is the entire surface.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Type
from app.runtime.state import WorkflowState  # Adjust the import path as necessary
from pydantic import BaseModel


class NodeType(ABC):
    # ----- declared by every subclass -----
    type_name: ClassVar[str]                    # registry key, e.g. "TransformAgent"
    input_schema: ClassVar[Type[BaseModel]]     # what this node reads from state
    output_schema: ClassVar[Type[BaseModel]]    # what this node writes back
    config_schema: ClassVar[Type[BaseModel]]    # YAML config shape, validated at load
    description: ClassVar[str] = ""             # shown in the Builder palette

    # ----- presentation metadata read by the Builder -----
    # "core" node types are the small reusable vocabulary a new workflow should
    # normally be built from; "specialized" ones are existing domain capabilities
    # that remain available but are not where an author starts. The palette
    # groups on this, which is how the user-facing mental model stays small
    # without deleting anything that works.
    family: ClassVar[str] = "specialized"

    # What kind of thing happens here: "ai" (a model decides), "deterministic"
    # (code decides, repeatably), "external" (something outside the platform
    # changes), "human" (a person decides), "input"/"output" (data crosses the
    # boundary). The canvas renders this so the automation boundary is visible
    # rather than buried in config.
    execution_kind: ClassVar[str] = "deterministic"

    # Concise, author-facing explanation rendered in the inspector's About tab:
    # keys `what`, `why`, `receives`, `produces`, `uses_ai`, `external_action`,
    # and optionally `presets` (configuration starting points, never new node
    # types) and `operators`.
    about: ClassVar[dict[str, Any]] = {}

    def __init__(self, node_id: str, raw_config: dict[str, Any],services: dict[str, Any] | None = None):
        # Pydantic validates the config on construction. If the YAML is wrong,
        # we fail at compile time, not in the middle of an LLM call.
        self.node_id = node_id
        self.config = self.config_schema(**raw_config)
        self.services = services or {}

    @abstractmethod
    async def run(
        self,
        state: "WorkflowState",          # forward ref to avoid circular import
        resolved_config: dict[str, Any], # config with {{...}} templates substituted
    ) -> dict[str, Any]:
        """Execute the node.

        Returns a dict that the runtime will write into
        state["node_outputs"][self.node_id]. The shape must conform to
        self.output_schema; the compiler will validate it before merging.
        """
        ...

    # ----- optional preflight extension points -----
    # Overriding these is how a new node type gets preflight coverage for
    # anything beyond the generic schema checks — no edits to
    # app/runtime/preflight.py required. See tests/test_node_preflight_coverage.py,
    # which forces every new registered node type to be reviewed against them.

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Service names this node needs at runtime, given its resolved
        config. Preflight aggregates these across all nodes to compute the
        workflow's `required_services`. Default: none."""
        return set()

    @classmethod
    def preflight_output_fields(cls, config: dict[str, Any]) -> set[str]:
        """Valid dotted template-reference suffixes for this node's output,
        beyond the static output_schema field names — e.g. a node whose
        output_schema declares a free-form dict but whose own YAML config
        further constrains which sub-keys are actually populated. Entries may
        be exact field names or dotted prefixes (e.g. "parsed.summary").
        Default: exactly the output_schema's declared field names."""
        return set(cls.output_schema.model_fields)

    @classmethod
    def preflight_static_output_values(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Output fields whose value preflight can already prove, without
        running the node — e.g. TransformAgent's "parsed" is always {} when
        its own config sets no output_schema. A bare template reference to
        such a field can only ever substitute that one fixed value, never
        real content, so preflight treats it as an authoring error rather
        than a warning (see TEMPLATE_STATICALLY_EMPTY_FIELD in
        app/runtime/preflight.py). Default: none — most node types have no
        statically-known-empty fields."""
        return {}