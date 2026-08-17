"""MCPToolAgent — call one capability on one MCP server.

This is the platform's integration primitive. Dynamics 365, and everything after
it, is reached through *this* node:

    MCP Tool
      Server   Dynamics 365
      Tool     Find Account
      Inputs   company_name ← outputs.understand_request.result.customer.company

A second instance with `tool: get_open_opportunities` is the same node type with
different configuration. There is no `DynamicsFindAccountNode`, and there will be
no `SalesforceFindAccountNode` either: a new CRM capability is a new MCP tool, a
new business process is workflow configuration, and neither is Python in this
repository.

Distinct from `MCPAgent`, which is an *autonomous* loop where a model chooses
which tools to call. That is a genuinely different capability with a genuinely
different risk profile — and it is exactly what should not be deciding whether to
write to a customer's CRM. This node calls the one tool the author selected, with
the arguments the author mapped, through the policy gate.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.integrations.operations import (
    AmbiguousOperationFailure,
    OperationInFlight,
)
from app.mcp.policy import MCPPolicyError
from app.mcp.service import MCPToolError
from app.nodes.approval import human_approved as _human_approved
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger

log = get_logger(__name__)


class MCPToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Configured connection id — never a URL, never a credential.
    server_id: str = Field(description="Which configured MCP server connection this step calls.")
    tool: str = Field(description="Which tool on that server to invoke.")
    #: Arguments for the tool, matching its discovered input schema. Values are
    #: normally template references the Builder's field picker wrote.
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments for the tool, matching its discovered input schema — normally template references from the field picker.",
    )

    #: When false, a failure becomes a routable fact instead of ending the run —
    #: the same choice the AI Task offers, and for the same reason: "the customer
    #: is not in the CRM" is a business outcome, not a crash.
    fail_on_error: bool = Field(
        default=True,
        description="When off, a tool failure becomes a routable status instead of stopping the run.",
    )

    #: Upper bound on this one call, overriding the tool policy's and the
    #: connection's. A step whose value is "enrich if you can, now" needs a
    #: tighter bound than the connection default: `fail_on_error: false` only
    #: promises the run survives a failure, not that it survives a *hang*, and
    #: an emergency route that waits a minute for an unresponsive ERP is
    #: indistinguishable to the person waiting from one that depends on it.
    timeout_seconds: float | None = Field(
        default=None, gt=0, le=600,
        description="Upper bound on this one call, overriding the connection's default timeout.",
    )

    #: The author's explicit statement that this write may happen without a
    #: human review in front of it. It cannot *grant* permission — the
    #: connection's write policy still governs — but a write that needs
    #: approval will not run just because a node was dropped on the canvas.
    allow_unattended_write: bool = Field(
        default=False,
        description="Explicit statement that this write may happen without a human review step in front of it. Does not override the connection's own write policy.",
    )

    #: Retries for read operations. Writes are never retried here; the ledger
    #: decides what a repeated write means.
    max_read_retries: int = Field(
        default=1, ge=0, le=3,
        description="Retries for read-only tool calls. Writes are never retried here.",
    )


class MCPToolInput(BaseModel):
    pass


class MCPToolOutput(BaseModel):
    """One output shape for every tool on every server.

    `data` holds the tool's typed result, so mapping reads
    `outputs.find_account.data.accounts.items.account_id` exactly like any other
    node's output. `found`/`count` are lifted out of collection results because
    "did the CRM know this customer?" is the question a routing rule actually
    asks, and it should not require a Transform to answer.
    """

    server: str = ""
    tool: str = ""
    operation: str = "unknown"
    status: str = "ok"          # ok | error | denied | needs_approval | skipped
    data: dict[str, Any] = Field(default_factory=dict)
    #: The first record of a collection result, or the single record itself.
    #: A CRM search returns matches, and "use the best match" is what a workflow
    #: nearly always means — so `{{outputs.find_account.first.account_id}}` is
    #: the readable form of `data.accounts.0.account_id`. `count` is right next
    #: to it so a workflow can notice that several accounts matched and send an
    #: ambiguous case to a person instead of silently taking the first.
    first: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    count: int = 0
    found: bool = False
    is_structured: bool = False
    mode: str = ""              # live | mock — never let a demo look like production
    duration_s: float = 0.0
    deduplicated: bool = False
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False
    suggested_action: str | None = None


@NodeRegistry.register
class MCPToolAgent(NodeType):
    type_name = "MCPToolAgent"
    description = (
        "Call one capability on a connected MCP server — CRM, ERP, or any other "
        "business system. The server exposes the tools; the workflow chooses."
    )
    input_schema = MCPToolInput
    output_schema = MCPToolOutput
    config_schema = MCPToolConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "external"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Invokes a named tool on a configured MCP server and returns its "
            "result as typed workflow data."
        ),
        "why": (
            "Business systems are reached through MCP, not through node types. "
            "A new CRM capability is a new MCP tool; the Builder discovers it "
            "with no change here and no change in the frontend."
        ),
        "receives": "Tool arguments, usually mapped from an earlier step.",
        "produces": "data (the tool's typed result), plus found/count and the operation class.",
        "uses_ai": False,
        "external_action": True,
        "safety": (
            "Read tools run freely. Write tools are refused unless the "
            "connection permits them, and — unless the author states otherwise "
            "— unless a human review runs first. A write that fails ambiguously "
            "is recorded, not retried."
        ),
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"mcp"}

    @classmethod
    def preflight_output_fields(cls, config: dict[str, Any]) -> set[str]:
        """Authorise the fixed envelope.

        The `data.*` sub-paths depend on the tool's declared output schema,
        which is known only by asking the server — so they are authorised as a
        prefix here and checked properly by the Builder's contract view, which
        can reach the server. Being honest about that boundary is better than
        pretending to validate what this hook cannot see.
        """
        # `data.*` and `first.*`: the sub-shape is defined by the *server's*
        # declared output schema, which preflight deliberately does not contact
        # — a Builder check must not depend on a CRM being reachable. The
        # Builder's tool-discovery panel, which can reach the server, validates
        # those paths properly.
        return set(MCPToolOutput.model_fields) | {"data", "data.*", "first", "first.*"}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = MCPToolConfig(**resolved_config)
        service = self.services.get("mcp")
        if service is None:
            raise RuntimeError(
                f"MCPToolAgent '{self.node_id}' needs the MCP integration "
                "service. No MCP server is configured in this deployment."
            )

        inputs = state.get("inputs") or {}
        run_id = str(inputs.get("SYSTEM.run_id") or "")
        session_id = str(state.get("session_id") or "")

        # An argument that resolved to nothing means the value this step depends
        # on does not exist — a CRM lookup found no account, so there is no
        # account id to fetch opportunities for. Calling anyway would send null
        # to the server and produce a confusing error; skipping reports the
        # truth, and `found: false` is exactly what the downstream rules read.
        #
        # But only a REQUIRED argument coming up empty means the call cannot
        # happen. An optional one that did not resolve is just a narrowing the
        # author offered and the message did not supply — a quote lookup mapped
        # to quotation_reference, account_id and an optional customer PO number
        # must still run for a customer who quoted no PO number. Those are
        # dropped from the call instead of cancelling it.
        arguments = dict(cfg.arguments)
        empty = _empty_arguments(arguments)
        if empty:
            required = await self._required_arguments(service, cfg)
            missing = [name for name in empty if name in required]
            if missing:
                log.info(
                    "mcp_tool.skipped",
                    node_id=self.node_id,
                    tool=cfg.tool,
                    missing=missing,
                )
                return self._skipped(cfg, missing)
            for name in empty:
                arguments.pop(name, None)
            log.info(
                "mcp_tool.optional_arguments_omitted",
                node_id=self.node_id,
                tool=cfg.tool,
                omitted=empty,
            )

        # Whether a human decision has actually happened on this run's path —
        # read from the run's own state, not asserted by this node's config. A
        # node cannot vouch for its own approval.
        approval_satisfied = (
            cfg.allow_unattended_write or _human_approved(state)
        )

        try:
            result = await service.call(
                server_id=cfg.server_id,
                tool_name=cfg.tool,
                arguments=arguments,
                run_id=run_id,
                node_id=self.node_id,
                session_id=session_id,
                approval_satisfied=approval_satisfied,
                audit_db=self.services.get("audit_db"),
                timeout_override=cfg.timeout_seconds,
            )
        except (MCPPolicyError, MCPToolError, OperationInFlight,
                AmbiguousOperationFailure) as error:
            return self._failure(cfg, error)

        data = result["data"]
        count, found, first = _summarise(data)
        return {
            "server": result["server"],
            "tool": result["tool"],
            "operation": result["operation"],
            "status": "ok",
            "data": data,
            "first": first,
            "text": result["text"],
            "count": count,
            "found": found,
            "is_structured": result["is_structured"],
            "mode": result["mode"],
            "duration_s": result["duration_s"],
            "deduplicated": result["deduplicated"],
            "error": None,
            "error_code": None,
            "retryable": False,
            "suggested_action": None,
        }

    async def _required_arguments(
        self, service: Any, cfg: MCPToolConfig
    ) -> set[str]:
        """The argument names the server itself declares as required.

        Falls back to "all of them" when the tool cannot be described — an
        unreachable server is not a reason to send it a null-valued argument
        it may well reject; the conservative answer keeps the old behaviour.
        """
        try:
            descriptor = await service.find_tool(cfg.server_id, cfg.tool)
        except Exception:
            descriptor = None
        if not descriptor:
            return set(cfg.arguments)
        schema = descriptor.get("input_schema") or {}
        required = schema.get("required")
        names = (
            {name for name in required if isinstance(name, str)}
            if isinstance(required, list)
            else set()
        )

        # anyOf: "satisfy at least one of these alternative requirement sets"
        # — a lookup keyed by, say, a quotation number OR a purchase-order
        # number. If none of the alternatives are satisfied by what this node
        # actually supplied, there is nothing left to search by, so every
        # field named across the alternatives becomes effectively required —
        # the call should skip rather than run empty-handed.
        any_of = schema.get("anyOf")
        if isinstance(any_of, list) and any_of:
            provided = {
                name
                for name, value in cfg.arguments.items()
                if not (value is None or (isinstance(value, str) and not value.strip()))
            }
            alternatives = [
                set(alt["required"])
                for alt in any_of
                if isinstance(alt, dict) and isinstance(alt.get("required"), list)
            ]
            if alternatives and not any(alt <= provided for alt in alternatives):
                names |= {name for alt in alternatives for name in alt}

        return names

    def _skipped(self, cfg: MCPToolConfig, missing: list[str]) -> dict[str, Any]:
        return {
            "server": cfg.server_id,
            "tool": cfg.tool,
            "operation": "read",
            "status": "skipped",
            "data": {},
            "first": {},
            "text": "",
            "count": 0,
            "found": False,
            "is_structured": False,
            "mode": "",
            "duration_s": 0.0,
            "deduplicated": False,
            "error": (
                f"Skipped: {', '.join(missing)} had no value, so there was "
                "nothing to look up."
            ),
            "error_code": "MCP_INPUT_UNAVAILABLE",
            "retryable": False,
            "suggested_action": (
                "This is normal when an earlier lookup found nothing. Add a "
                "rule for the case where it does."
            ),
        }

    def _failure(self, cfg: MCPToolConfig, error: Exception) -> dict[str, Any]:
        code = getattr(error, "code", None) or type(error).__name__
        status = {
            "MCP_APPROVAL_REQUIRED": "needs_approval",
            "MCP_TOOL_NOT_ALLOWED": "denied",
            "MCP_ROLE_NOT_ALLOWED": "denied",
            "MCP_WRITE_NOT_PERMITTED": "denied",
        }.get(code, "error")

        log.warning(
            "mcp_tool.failed",
            node_id=self.node_id,
            server=cfg.server_id,
            tool=cfg.tool,
            code=code,
        )

        if cfg.fail_on_error:
            raise RuntimeError(
                f"MCPToolAgent '{self.node_id}' failed ({code}): {error}"
            ) from error

        return {
            "server": cfg.server_id,
            "tool": cfg.tool,
            "operation": "unknown",
            "status": status,
            "data": {},
            "first": {},
            "text": "",
            "count": 0,
            # False, explicitly: a failed CRM lookup means "we do not know this
            # customer exists", and a downstream rule must read it that way
            # rather than inheriting a stale or absent value.
            "found": False,
            "is_structured": False,
            "mode": "",
            "duration_s": 0.0,
            "deduplicated": False,
            "error": str(error)[:800],
            "error_code": code,
            "retryable": bool(getattr(error, "retryable", False)),
            "suggested_action": getattr(error, "suggested_action", "") or None,
        }


def _empty_arguments(arguments: dict[str, Any]) -> list[str]:
    """Argument names that were supplied but resolved to nothing.

    Only *supplied* arguments are checked: an argument the author deliberately
    left out is not this node's business (the tool's own schema decides whether
    it is required). What this catches is an argument that was mapped from an
    upstream value which turned out to be absent — the difference between "I
    did not ask for an account id" and "I asked for one and there wasn't one".
    """
    empty: list[str] = []
    for name, value in arguments.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            empty.append(name)
    return empty


def _summarise(data: dict[str, Any]) -> tuple[int, bool, dict[str, Any]]:
    """Lift `count`, `found` and `first` out of whatever shape the tool returned.

    Collection results carry an explicit `count`; a single-record result is one
    thing. Both shapes are common across servers, and a routing rule should not
    have to know which one a given tool happens to use.
    """
    if not data:
        return 0, False, {}

    for key, value in data.items():
        if key.startswith("_") or not isinstance(value, list):
            continue
        count = (
            int(data["count"]) if isinstance(data.get("count"), int) else len(value)
        )
        first = value[0] if value and isinstance(value[0], dict) else {}
        return count, count > 0, first

    # A single-record result: either wrapped under one key, or the object itself.
    payloads = [
        value
        for key, value in data.items()
        if isinstance(value, dict) and not key.startswith("_")
    ]
    if len(payloads) == 1:
        return 1, True, payloads[0]
    return 1, True, {
        key: value for key, value in data.items() if not key.startswith("_")
    }


