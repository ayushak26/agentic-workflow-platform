"""EndAgent: the canonical workflow exit point.

Defines what the workflow returns or presents when execution finishes. End
never performs RAG, calls an ERP, runs another agent, or makes decisions —
those already happened upstream. End's entire job is: collect the values an
earlier node already produced, and present them as the workflow's result.

Every value-bearing config field is a plain string resolved through the
existing `{{...}}` templating (app/runtime/templating.py) before this node
ever sees it — resolved_config already has literals substituted, exactly
like every other node's config. No new expression language.

Three modes:
- workflow_result (default): generic key -> value mapping. Not RAG-specific,
  not chat-specific — usable by any workflow (order lookups, ERP results,
  product recommendations, ...).
- chat_response: a first-class outcome/message/route_to/sources/handoff
  shape for chatbot workflows, including ones that hand a conversation off
  to a department (see workflows/routed_support_sample.yaml).
- custom_response: a title + a single free-form message, for a more
  human-readable final result than a bag of key/value pairs.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


class EndOutputField(BaseModel):
    """Pydantic model defining the EndOutputField shape.

    Attributes:
        key (str).
        value_from (Any).
    """
    key: str
    # A "{{...}}" reference (or literal), resolved before run() sees it. Typed
    # Any, not str — whole-value template resolution preserves the referenced
    # field's real type (a list, an object, a file reference, ...), and a
    # workflow_result output is explicitly allowed to be any of those (§29).
    value_from: Any = None


class EndConfig(BaseModel):
    """Pydantic model defining the EndConfig shape.

    Attributes:
        mode (str).
        outputs (list[EndOutputField]).
        title (str).
        message (str).
        chat_message (str).
        outcome (str).
        route_to (str | None).
        route_to_label (str | None).
    """
    mode: str = "workflow_result"  # "workflow_result" | "chat_response" | "custom_response"

    # workflow_result mode
    outputs: list[EndOutputField] = Field(default_factory=list)

    # custom_response mode
    title: str = ""
    message: str = ""

    # chat_response mode
    chat_message: str = ""
    outcome: str = "reply"          # expected "reply" | "route" — not enforced, just the two conventional values
    route_to: str | None = None
    route_to_label: str | None = None
    # Typed Any, not str — this is normally a RAG Agent's `sources` list,
    # resolved through the same whole-value "{{...}}" substitution as
    # everything else here, which preserves the list/object shape.
    sources: Any = None
    handoff: dict[str, Any] = Field(default_factory=dict)


class EndInput(BaseModel):
    """Pydantic model defining the EndInput shape."""
    pass


class EndOutput(BaseModel):
    """Pydantic model defining the EndOutput shape.

    Attributes:
        result (dict[str, Any]).
    """
    result: dict[str, Any] = Field(default_factory=dict)


def _humanize(value: str) -> str:
    """Internal helper for the humanize step.

    Args:
        value (str): Value to process.

    Returns:
        str: The result.
    """
    return value.replace("_", " ").replace("-", " ").strip().title()


@NodeRegistry.register
class EndAgent(NodeType):
    """Workflow node type implementing the EndAgent capability.

    Attributes:
        family (ClassVar[str]).
        execution_kind (ClassVar[str]).
        about (ClassVar[dict[str, Any]]).
    """
    type_name = "EndAgent"
    description = "What the workflow returns or shows when it finishes."
    input_schema = EndInput
    output_schema = EndOutput
    config_schema = EndConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "output"
    about: ClassVar[dict[str, Any]] = {
        "what": "Collects earlier outputs and presents them as the workflow's final result.",
        "why": "Gives every workflow a clear, visible destination for its result.",
        "receives": "Values mapped from any earlier node's output.",
        "produces": "result — a flat dict, shaped by the selected response mode.",
        "uses_ai": False,
        "external_action": False,
        "presets": [
            {
                "id": "workflow_result",
                "label": "Workflow Result",
                "summary": "Return mapped workflow outputs as structured data.",
                "config": {"mode": "workflow_result"},
            },
            {
                "id": "chat_response",
                "label": "Chat Response",
                "summary": "Reply to (or route) a chatbot conversation.",
                "config": {"mode": "chat_response"},
            },
            {
                "id": "custom_response",
                "label": "Custom Response",
                "summary": "A human-friendly title and message.",
                "config": {"mode": "custom_response"},
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
        declared = {"result"}
        try:
            cfg = EndConfig(**config)
        except Exception:
            return declared
        if cfg.mode == "workflow_result":
            declared |= {f"result.{item.key}" for item in cfg.outputs}
        elif cfg.mode == "custom_response":
            declared |= {"result.title", "result.message"}
        else:
            declared |= {"result.outcome", "result.message"}
            if cfg.route_to:
                declared |= {"result.route_to", "result.route_to_label"}
            if cfg.sources:
                declared |= {"result.sources"}
            if cfg.handoff:
                declared |= {"result.handoff"}
        return declared

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = EndConfig(**resolved_config)

        if cfg.mode == "workflow_result":
            result = {item.key: item.value_from for item in cfg.outputs}
        elif cfg.mode == "custom_response":
            result = {"title": cfg.title, "message": cfg.message}
        else:
            result = {"outcome": cfg.outcome, "message": cfg.chat_message}
            if cfg.route_to:
                result["route_to"] = cfg.route_to
                result["route_to_label"] = cfg.route_to_label or _humanize(cfg.route_to)
            if cfg.sources:
                result["sources"] = cfg.sources
            if cfg.handoff:
                result["handoff"] = dict(cfg.handoff)

        return {"result": result}
