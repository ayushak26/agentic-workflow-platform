"""HumanInLoopAgent: pause and wait for user decision.

LangGraph's interrupt() raises a GraphInterrupt that the runtime catches.
State is checkpointed at the pause. Resuming with Command(resume=value)
returns `value` from inside the interrupt() call as if it had returned
normally."""
from __future__ import annotations

from copy import deepcopy
from html import escape
from html.parser import HTMLParser
import json
from typing import Any, Literal

from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.runtime.schema import WorkflowFileRef
from app.workflow.file_inputs import validate_workflow_file_reference


class HITLConfig(BaseModel):
    question: str                                  # shown to the human, templated
    context_fields: list[str] = Field(default_factory=list)
    # paths into state, e.g. ["rfp_intel.parsed.requirements"]
    editable_content_field: str | None = Field(
        default=None,
        description=(
            "Optional state path shown in the rich-text editor. When the human "
            "saves an edit, the value at this path is replaced before downstream "
            "nodes run. If omitted, the first context field containing content "
            "is used."
        ),
    )
    allow_document_override: bool = Field(
        default=True,
        description=(
            "Allow one uploaded, text-extractable document to replace the editor "
            "content before the human continues."
        ),
    )
    max_edit_chars: int = Field(
        default=1_000_000,
        ge=1_000,
        le=2_000_000,
        description="Maximum plain-text characters accepted from the editor.",
    )
    allowed_actions: list[Literal["approve", "reject", "edit"]] = Field(
        default_factory=lambda: ["approve", "reject", "edit"]
    )


class HITLInput(BaseModel):
    pass


class HITLReviewContent(BaseModel):
    """Canonical review document passed between the UI and workflow runtime."""

    text: str = Field(max_length=2_000_000)
    html: str | None = Field(default=None, max_length=4_000_000)
    source: Literal["workflow", "editor", "upload"] = "workflow"
    source_path: str | None = None
    source_document: WorkflowFileRef | None = None


class HITLOutput(BaseModel):
    decision: Literal["approve", "reject", "edit"]
    reason: str | None = None                      # set on reject
    content: HITLReviewContent | None = None
    # Kept as a separate field for backward compatibility with existing
    # downstream templates. It is populated only when decision == "edit".
    edited_content: HITLReviewContent | None = None
    content_overridden: bool = False


class HITLInterruptPayload(BaseModel):
    """What the Cockpit sees when this node pauses."""
    node_id: str
    question: str
    context: dict[str, Any]
    allowed_actions: list[str]
    content: HITLReviewContent | None = None
    allow_document_override: bool = True
    max_edit_chars: int = 1_000_000


@NodeRegistry.register
class HumanInLoopAgent(NodeType):
    type_name = "HumanInLoopAgent"
    description = "Pause for human approval, rejection, or edit."
    input_schema = HITLInput
    output_schema = HITLOutput
    config_schema = HITLConfig

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = HITLConfig(**resolved_config)

        # Collect requested context for the Cockpit display
        context = {}
        for path in cfg.context_fields:
            try:
                context[path] = _resolve_path(path, state)
            except KeyError:
                context[path] = None

        review_content = _review_content(cfg, state, context)
        payload = HITLInterruptPayload(
            node_id=self.node_id,
            question=cfg.question,
            context=context,
            allowed_actions=cfg.allowed_actions,
            content=review_content,
            allow_document_override=cfg.allow_document_override,
            max_edit_chars=cfg.max_edit_chars,
        ).model_dump()

        # A restart-safe resume recompiles the graph and replays completed
        # nodes from Mongo. When that replay reaches the paused gate, the API-
        # validated decision is injected here. Normal in-process resumes still
        # use LangGraph's Command(resume=...) path.
        durable_decisions = self.services.get("hitl_resume_decisions") or {}
        if self.node_id in durable_decisions:
            user_decision = durable_decisions[self.node_id]
        else:
            # PAUSE — execution suspends here. The caller sees __interrupt__.
            user_decision = interrupt(payload)

        # Validate the decision shape
        decision = user_decision.get("decision")
        if decision not in cfg.allowed_actions:
            raise ValueError(
                f"HITL node {self.node_id} got disallowed decision: {decision!r}"
            )

        edited_content: HITLReviewContent | None = None
        content = review_content
        state_patch: dict[str, Any] = {}
        if decision == "edit":
            raw_edit = user_decision.get("edited_content")
            if raw_edit is None:
                raise ValueError(
                    f"HITL node {self.node_id} requires edited_content "
                    "when decision is 'edit'"
                )
            edited_content = HITLReviewContent.model_validate(raw_edit)
            if len(edited_content.text) > cfg.max_edit_chars:
                raise ValueError(
                    f"HITL node {self.node_id} edit exceeds "
                    f"{cfg.max_edit_chars} characters"
                )

            # Never trust client-supplied state paths. The server decides which
            # workflow field can be replaced from the paused node's own config.
            source_path = review_content.source_path if review_content else None
            edited_content.source_path = source_path
            edited_content.html = sanitize_rich_html(edited_content.html)

            if edited_content.source_document is not None:
                if not cfg.allow_document_override:
                    raise ValueError(
                        f"HITL node {self.node_id} does not allow document override"
                    )
                await validate_workflow_file_reference(
                    edited_content.source_document,
                    session_id=str(state.get("session_id") or ""),
                    object_store=self.services.get("object_store"),
                    require_parseable_text=True,
                )

            content = edited_content
            if source_path:
                state_patch = _patch_reviewed_content(
                    state,
                    source_path,
                    edited_content.text,
                )

        output = {
            "decision": decision,
            "reason": user_decision.get("reason"),
            "content": content.model_dump() if content else None,
            "edited_content": (
                edited_content.model_dump()
                if edited_content is not None
                else None
            ),
            "content_overridden": bool(
                edited_content is not None
                and edited_content.source_document is not None
            ),
        }
        if state_patch:
            output["__state__"] = state_patch
        return output


def _resolve_path(path: str, state: dict) -> Any:
    parts = path.split(".")
    node_outputs = state.get("node_outputs", {})
    cursor: Any = node_outputs if parts[0] in node_outputs else state
    for p in parts:
        if isinstance(cursor, dict):
            cursor = cursor[p]
        else:
            cursor = getattr(cursor, p)
    return cursor


def _review_content(
    cfg: HITLConfig,
    state: dict[str, Any],
    context: dict[str, Any],
) -> HITLReviewContent | None:
    candidates = (
        [cfg.editable_content_field]
        if cfg.editable_content_field
        else list(cfg.context_fields)
    )
    for path in candidates:
        try:
            value = _resolve_path(path, state)
        except (KeyError, AttributeError, TypeError):
            value = context.get(path)
        if value is None:
            continue
        return HITLReviewContent(
            text=_content_as_text(value),
            source="workflow",
            source_path=path,
        )
    return None


def _content_as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _patch_reviewed_content(
    state: dict[str, Any],
    path: str,
    text: str,
) -> dict[str, Any]:
    """Replace only the configured editable path for downstream nodes."""

    parts = path.split(".")
    if not parts or not parts[0]:
        raise ValueError("editable_content_field cannot be empty")

    if parts[0] == "inputs":
        if len(parts) < 2 or parts[1].startswith("SYSTEM"):
            raise ValueError("editable_content_field cannot replace system inputs")
        inputs = deepcopy(state.get("inputs") or {})
        _set_nested(inputs, parts[1:], text)
        return {"inputs": inputs}

    node_outputs = state.get("node_outputs") or {}
    node_id = parts[0]
    if node_id not in node_outputs:
        raise ValueError(
            f"editable_content_field must reference inputs or an existing "
            f"node output; got {path!r}"
        )
    if len(parts) < 2:
        raise ValueError(
            "editable_content_field must reference a field inside a node output"
        )
    node_value = deepcopy(node_outputs[node_id])
    _set_nested(node_value, parts[1:], text)
    return {"node_outputs": {node_id: node_value}}


def _set_nested(container: Any, parts: list[str], value: str) -> None:
    cursor = container
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            raise ValueError(
                "editable_content_field must resolve through existing objects"
            )
        cursor = cursor[part]
    if not isinstance(cursor, dict):
        raise ValueError(
            "editable_content_field parent must be an object"
        )
    cursor[parts[-1]] = value


class _RichTextSanitizer(HTMLParser):
    """Small allow-list sanitizer for browser-generated editor markup."""

    allowed_tags = {
        "p", "br", "strong", "b", "em", "i", "u", "s", "h1", "h2", "h3",
        "ul", "ol", "li", "blockquote", "hr", "pre", "code",
    }
    void_tags = {"br", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag in self.allowed_tags:
            self.parts.append(f"<{tag}>")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag in self.void_tags:
            self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.allowed_tags and tag not in self.void_tags:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data))


def sanitize_rich_html(value: str | None) -> str | None:
    if not value:
        return None
    parser = _RichTextSanitizer()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)
