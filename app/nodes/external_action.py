"""ExternalActionAgent — call a REST API or a webhook, kept apart from MCP.

MCP is how this platform reaches a *connected* business system whose tools
are discovered from a server (Dynamics, the business-records database) — a
new capability there is a new MCP tool, not a new node type. External Action
is the other case: a specific URL the author already knows, one this
deployment has no MCP server for. Two distinct outward-facing modes, kept as
one node type with a mode selector because the underlying mechanics (method,
URL, headers, body, response) are identical — only the authoring framing and
the default method differ:

    rest_api  A request/response integration call — the response body is
              consumed by a later step.
    webhook   A fire-and-forget outbound notification — defaults to POST,
              the response is not normally consumed.

Every instance of this node must state its own safety class explicitly —
`read`, `write`, or `external_action` — because that is the whole point of
§48's classification spec: the decision must be visible in configuration and
in execution UI, not buried in what the URL happens to do.
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.integrations.external_action import (
    ExternalActionError,
)
from app.integrations.operations import (
    AmbiguousOperationFailure,
    OperationInFlight,
)
from app.nodes.approval import human_approved
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger

log = get_logger(__name__)

_HTTPMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


class ExternalActionConfig(BaseModel):
    """Pydantic model defining the ExternalActionConfig shape.

    Attributes:
        action_type (Literal['rest_api', 'webhook']).
        safety_class (Literal['read', 'write', 'external_action']).
        method (_HTTPMethod).
        url (str).
        headers (dict[str, str]).
        body (Any).
        timeout_seconds (float).
        allow_unattended_write (bool).
    """
    model_config = ConfigDict(extra="forbid")

    action_type: Literal["rest_api", "webhook"] = Field(
        description=(
            "rest_api: a request/response call whose response matters downstream. "
            "webhook: a fire-and-forget outbound notification."
        ),
    )
    #: No default, deliberately — omitting this is a config error, not a
    #: silent guess. This is the field the classification spec exists for.
    safety_class: Literal["read", "write", "external_action"] = Field(
        description=(
            "How this call is classified: read (nothing changes), write (a "
            "business record changes), external_action (a generic outward "
            "effect — a notification, a trigger — that is neither). Must be "
            "stated explicitly."
        ),
    )
    method: _HTTPMethod = Field(
        default="POST",
        description="HTTP method. Webhooks default to POST; a REST API call may use any of these.",
    )
    url: str = Field(
        description="Target URL — template-resolved like any other config string.",
    )
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = Field(
        default=None,
        description="Request body, sent as JSON. Usually a mapped object from an earlier step.",
    )
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    #: Same escape-hatch shape as MCPToolConfig's own — states that this
    #: particular step may act without a human review in front of it. Cannot
    #: grant permission the platform itself refuses; it only lets a write
    #: that the author has actually thought about run unattended.
    allow_unattended_write: bool = Field(
        default=False,
        description=(
            "Explicit statement that this call may run without a human review "
            "step in front of it, when safety_class is write or external_action."
        ),
    )
    #: Same scoping as MCPToolConfig's own — names the specific review this
    #: call is gated by, instead of accepting any approval anywhere on the run.
    approved_by: str | None = Field(
        default=None,
        description="Node id of the HumanInLoopAgent review that gates this call. When set, only that node's decision counts.",
    )


class ExternalActionInput(BaseModel):
    """Pydantic model defining the ExternalActionInput shape."""
    pass


class ExternalActionOutput(BaseModel):
    """Pydantic model defining the ExternalActionOutput shape.

    Attributes:
        status (Literal['ok', 'error', 'needs_approval']).
        safety_class (str).
        response_status (int | None).
        response_body (Any).
        duration_s (float).
        deduplicated (bool).
        error (str | None).
        error_code (str | None).
    """
    status: Literal["ok", "error", "needs_approval"] = "ok"
    safety_class: str = ""
    response_status: int | None = None
    response_body: Any = None
    duration_s: float = 0.0
    deduplicated: bool = False
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False


@NodeRegistry.register
class ExternalActionAgent(NodeType):
    """Workflow node type implementing the ExternalActionAgent capability.

    Attributes:
        family (ClassVar[str]).
        execution_kind (ClassVar[str]).
        about (ClassVar[dict[str, Any]]).
    """
    type_name = "ExternalActionAgent"
    description = (
        "Call an external REST API or send a webhook. For a system this "
        "deployment has no MCP connection for — a specific URL the author "
        "already knows."
    )
    input_schema = ExternalActionInput
    output_schema = ExternalActionOutput
    config_schema = ExternalActionConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "external"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Sends one HTTP request to a URL outside the platform — a REST "
            "API call or a webhook notification — and returns the response."
        ),
        "why": (
            "Not every outward action goes through a connected MCP server. "
            "This is the generic escape hatch for a specific URL, kept "
            "separate so it never gets confused with the classified, "
            "discoverable tools MCP already provides."
        ),
        "receives": "Method, URL, headers, and body — usually mapped from earlier steps.",
        "produces": "response_status, response_body, and the safety_class carried through for audit.",
        "uses_ai": False,
        "external_action": True,
        "safety": (
            "Read calls run freely. Write and external_action calls are "
            "refused unless the author states allow_unattended_write or a "
            "human review runs first on this run's path. A call that fails "
            "ambiguously is recorded, not retried."
        ),
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return {"external_action"}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = ExternalActionConfig(**resolved_config)
        service = self.services.get("external_action")
        if service is None:
            raise RuntimeError(
                f"ExternalActionAgent '{self.node_id}' needs the "
                "external_action service, which should always be configured."
            )

        inputs = state.get("inputs") or {}
        run_id = str(inputs.get("SYSTEM.run_id") or "")

        approval_satisfied = cfg.allow_unattended_write or human_approved(
            state, approved_by=cfg.approved_by
        )

        try:
            result = await service.call(
                run_id=run_id,
                node_id=self.node_id,
                safety_class=cfg.safety_class,
                method=cfg.method,
                url=cfg.url,
                headers=cfg.headers,
                body=cfg.body,
                timeout_seconds=cfg.timeout_seconds,
                approval_satisfied=approval_satisfied,
            )
        except (ExternalActionError, OperationInFlight, AmbiguousOperationFailure) as error:
            return self._failure(cfg, error)

        return {
            "status": "ok",
            "safety_class": cfg.safety_class,
            "response_status": result["response_status"],
            "response_body": result["response_body"],
            "duration_s": result["duration_s"],
            "deduplicated": result["deduplicated"],
            "error": None,
            "error_code": None,
            "retryable": False,
        }

    def _failure(self, cfg: ExternalActionConfig, error: Exception) -> dict[str, Any]:
        """Internal helper for the failure step.

        Args:
            cfg (ExternalActionConfig): The cfg.
            error (Exception): Error value or message.

        Returns:
            dict[str, Any]: The result.
        """
        code = getattr(error, "code", None) or type(error).__name__
        status = "needs_approval" if code == "EXTERNAL_ACTION_APPROVAL_REQUIRED" else "error"

        log.warning(
            "external_action.failed",
            node_id=self.node_id,
            url=cfg.url,
            code=code,
        )

        return {
            "status": status,
            "safety_class": cfg.safety_class,
            "response_status": None,
            "response_body": None,
            "duration_s": 0.0,
            "deduplicated": False,
            "error": str(error)[:800],
            "error_code": code,
            "retryable": bool(getattr(error, "retryable", False)),
        }
