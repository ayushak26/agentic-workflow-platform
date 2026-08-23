"""The MCP integration service: discovery, policy, execution, audit.

One object the rest of the platform talks to. It owns the sequence every tool
call goes through, so that sequence exists in exactly one place rather than
being re-implemented by each caller:

    describe servers → discover tools → classify → policy gate
    → idempotency reservation (writes) → execute → normalise → audit

The Builder reads discovery from here, the MCP Tool node executes through here,
and preflight classifies through here. A tool that the Builder shows as READ and
the runtime treats as a write would be a security bug, and the only reliable way
to prevent it is to have one implementation.
"""
from __future__ import annotations

import asyncio

import time
from typing import Any

from app.integrations.operations import (
    AmbiguousOperationFailure,
    ExternalOperationLedger,
    OperationInFlight,
    operation_key,
)
from app.mcp.policy import PolicyDecision, evaluate
from app.mcp.registry import (
    MCPServerConnection,
    MCPServerRegistry,
    OperationClass,
)
from app.mcp.results import MCPToolResult, field_paths_from_schema, normalise_result
from app.observability.logging import get_logger

log = get_logger(__name__)


class MCPToolError(RuntimeError):
    """A tool call failed in a way worth reporting to a workflow author.

    Carries a code, whether a retry could help, and what to do about it — so a
    workflow can route on the failure and a person can act on it, instead of
    both being handed "something went wrong" (§28).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "MCP_TOOL_ERROR",
        retryable: bool = False,
        suggested_action: str = "",
        server_id: str = "",
        tool_name: str = "",
    ):
        """Initialize the MCPToolError.

        Args:
            message (str): Message text.
            code (str): The code (optional, default 'MCP_TOOL_ERROR').
            retryable (bool): The retryable (optional, default False).
            suggested_action (str): The suggested action (optional, default '').
            server_id (str): The server id (optional, default '').
            tool_name (str): The tool name (optional, default '').
        """
        self.code = code
        self.retryable = retryable
        self.suggested_action = suggested_action
        self.server_id = server_id
        self.tool_name = tool_name
        super().__init__(message)

    def as_payload(self) -> dict[str, Any]:
        """Compute the as payload.

        Returns:
            dict[str, Any]: The payload.
        """
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "suggested_action": self.suggested_action,
            "server": self.server_id,
            "tool": self.tool_name,
        }


class ToolDescriptor(dict):
    """A discovered tool, described for the Builder.

    A dict subclass rather than a model so it serialises straight through the
    API without a second schema to keep in step.
    """


class MCPIntegrationService:
    """Provides the MCPIntegrationService behaviour."""
    def __init__(
        self,
        *,
        registry: MCPServerRegistry,
        client: Any,
        ledger: ExternalOperationLedger | None = None,
    ):
        """Initialize the MCPIntegrationService.

        Args:
            registry (MCPServerRegistry): The registry.
            client (Any): Client instance.
            ledger (ExternalOperationLedger | None): Operation ledger (optional, default None).
        """
        self.registry = registry
        self.client = client
        self.ledger = ledger or ExternalOperationLedger(collection="mcp_operations")
        self._tool_cache: dict[str, list[ToolDescriptor]] = {}

    # -- discovery -------------------------------------------------------

    def describe_servers(self) -> list[dict[str, Any]]:
        """Connections for the Builder's server picker. No credentials — only
        which variables are expected and whether they are set."""
        described = self.registry.describe_all()
        running = set(getattr(self.client, "running_servers", ()) or ())
        for entry in described:
            entry["running"] = entry["id"] in running
        return described

    async def discover_tools(
        self, server_id: str, *, refresh: bool = False
    ) -> list[ToolDescriptor]:
        """Ask the server what it can do.

        Never a hardcoded list: a tool added to the MCP server appears in the
        Builder with no frontend change, which is the whole reason MCP is the
        extension mechanism rather than a node type per capability.
        """
        connection = self.registry.require(server_id)
        if not refresh and server_id in self._tool_cache:
            return self._tool_cache[server_id]

        try:
            tools = await self.client.list_tools(server_id)
        except Exception as error:
            self.registry.record_health(
                server_id, healthy=False, error=f"{type(error).__name__}: {error}"
            )
            raise MCPToolError(
                f"Could not reach {connection.label}: {error}",
                code="MCP_SERVER_UNAVAILABLE",
                retryable=True,
                suggested_action=(
                    "Check the connection, then reconnect from the MCP servers "
                    "panel."
                ),
                server_id=server_id,
            ) from error

        described = [
            self._describe_tool(connection, tool)
            for tool in tools
            if connection.permits_tool(getattr(tool, "name", ""))
        ]
        self._tool_cache[server_id] = described
        self.registry.record_health(
            server_id, healthy=True, tool_count=len(described)
        )
        return described

    def _describe_tool(
        self, connection: MCPServerConnection, tool: Any
    ) -> ToolDescriptor:
        """Internal helper for the describe tool step.

        Args:
            connection (MCPServerConnection): The connection.
            tool (Any): The tool.

        Returns:
            ToolDescriptor: The tool.
        """
        from app.mcp.policy import classify_tool

        name = getattr(tool, "name", "")
        annotations = getattr(tool, "annotations", None)
        operation: OperationClass = classify_tool(
            name, connection=connection, annotations=annotations
        )
        policy = connection.policy_for(name)
        meta = getattr(tool, "meta", None) or {}
        eurskem_meta = meta.get("eurskem", {}) if isinstance(meta, dict) else {}
        output_schema = getattr(tool, "outputSchema", None)

        requires_approval = (
            policy.requires_approval
            if policy.requires_approval is not None
            else operation != "read" and connection.write_policy == "require_approval"
        )

        return ToolDescriptor(
            {
                "server_id": connection.id,
                "server_label": connection.label,
                "name": name,
                "title": getattr(tool, "title", None) or _humanise(name),
                "description": policy.description or getattr(tool, "description", "") or "",
                "operation": operation,
                "external_action": operation != "read",
                "requires_approval": requires_approval,
                "system": eurskem_meta.get("system") or connection.label,
                "typical_uses": (
                    policy.typical_uses or eurskem_meta.get("typical_uses") or []
                ),
                "mode": eurskem_meta.get("mode")
                or ("mock" if connection.is_mock else "live"),
                "input_schema": getattr(tool, "inputSchema", None) or {},
                "output_schema": output_schema or {},
                # Typed paths for the mapping picker, when the server declared
                # an output schema. Without one, a workflow can still map
                # `data.<whatever>` — it just cannot be checked in advance.
                "output_fields": field_paths_from_schema(output_schema),
            }
        )

    async def find_tool(
        self, server_id: str, tool_name: str
    ) -> ToolDescriptor | None:
        """Find the tool.

        Args:
            server_id (str): The server id.
            tool_name (str): The tool name.

        Returns:
            ToolDescriptor | None: The tool.
        """
        for tool in await self.discover_tools(server_id):
            if tool["name"] == tool_name:
                return tool
        return None

    async def health_check(self, server_id: str) -> dict[str, Any]:
        """Compute the health check.

        Args:
            server_id (str): The server id.

        Returns:
            dict[str, Any]: The check.
        """
        try:
            tools = await self.discover_tools(server_id, refresh=True)
        except MCPToolError as error:
            return {
                "server_id": server_id,
                "healthy": False,
                "tool_count": 0,
                "error": str(error),
            }
        return {
            "server_id": server_id,
            "healthy": True,
            "tool_count": len(tools),
            "error": None,
        }

    # -- execution -------------------------------------------------------

    async def call(
        self,
        *,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        run_id: str = "",
        node_id: str = "",
        session_id: str = "",
        user_role: str | None = None,
        approval_satisfied: bool = False,
        audit_db: Any = None,
        timeout_override: float | None = None,
    ) -> dict[str, Any]:
        """Run one tool through the full gate."""
        connection = self._require_connection(server_id, tool_name)
        descriptor = await self.find_tool(server_id, tool_name)
        if descriptor is None:
            available = [item["name"] for item in await self.discover_tools(server_id)]
            raise MCPToolError(
                f"{connection.label} does not expose a tool called {tool_name!r}. "
                f"Available: {available}.",
                code="MCP_UNKNOWN_TOOL",
                retryable=False,
                suggested_action="Pick a tool from the discovered list.",
                server_id=server_id,
                tool_name=tool_name,
            )

        decision = evaluate(
            connection=connection,
            tool_name=tool_name,
            annotations=None,
            user_role=user_role,
            approval_satisfied=approval_satisfied,
        )
        # The descriptor already classified the tool using the server's
        # annotations; carry that forward so the gate and the Builder agree.
        decision = PolicyDecision(
            allowed=decision.allowed,
            operation=descriptor["operation"],
            requires_approval=decision.requires_approval,
            reason=decision.reason,
            code=decision.code,
        )
        if not decision.allowed:
            log.warning(
                "mcp.tool_denied",
                server=server_id,
                tool=tool_name,
                code=decision.code,
                run_id=run_id,
            )
            await self._audit(
                audit_db,
                run_id=run_id,
                node_id=node_id,
                session_id=session_id,
                server_id=server_id,
                tool_name=tool_name,
                operation=decision.operation,
                status="denied",
                started=time.time(),
                detail={"code": decision.code},
            )
            decision.raise_if_denied()

        writing = decision.operation != "read"
        started = time.time()
        key = ""
        if writing:
            # Reserved *before* the call, so an ambiguous failure leaves a
            # durable "this may have happened" record rather than a silence the
            # next retry would turn into a duplicate CRM record (§30).
            key = operation_key(
                scope=f"{run_id}:{node_id}",
                target=f"{server_id}:{tool_name}",
                payload=arguments,
            )
            existing = await self.ledger.reserve(
                key,
                {
                    "status": "in_flight",
                    "server_id": server_id,
                    "tool": tool_name,
                    "operation": decision.operation,
                    "run_id": run_id,
                    "node_id": node_id,
                },
            )
            if existing is not None:
                return self._replay_or_refuse(
                    existing, key, connection, tool_name, decision
                )

        timeout_seconds = (
            timeout_override
            or connection.policy_for(tool_name).timeout_seconds
            or connection.timeout_seconds
        )
        try:
            # Enforced here rather than left to the client: the bound is part of
            # this gate's contract, and a client that ignores it (or a transport
            # that stalls below its own timeout) would otherwise make the
            # promise unenforceable.
            async with asyncio.timeout(timeout_seconds):
                response = await self.client.call_tool_raw(
                    tool_name,
                    arguments,
                    server=server_id,
                    timeout_seconds=timeout_seconds,
                )
        except TimeoutError as error:
            if writing:
                await self.ledger.mark_ambiguous(key, str(error))
                raise AmbiguousOperationFailure(
                    f"{tool_name} on {connection.label} timed out. It may have "
                    "changed data. Check the record before retrying "
                    f"(operation key {key[:12]})."
                ) from error
            raise MCPToolError(
                f"{tool_name} on {connection.label} timed out.",
                code="MCP_TOOL_TIMEOUT",
                retryable=True,
                suggested_action="Retry, or raise the connection's timeout.",
                server_id=server_id,
                tool_name=tool_name,
            ) from error
        except Exception as error:
            if writing:
                # A transport-level failure before a response is definitive
                # enough to release: nothing was acknowledged.
                await self.ledger.release(key, str(error))
            raise MCPToolError(
                f"{tool_name} on {connection.label} failed: {error}",
                code="MCP_TOOL_FAILED",
                retryable=False,
                server_id=server_id,
                tool_name=tool_name,
            ) from error

        result = normalise_result(
            response,
            max_bytes=connection.max_result_bytes,
            tool_name=tool_name,
        )
        duration = time.time() - started

        error_payload = _tool_error_payload(result)
        if error_payload is not None:
            if writing:
                await self.ledger.release(key, error_payload.get("code", "error"))
            await self._audit(
                audit_db,
                run_id=run_id,
                node_id=node_id,
                session_id=session_id,
                server_id=server_id,
                tool_name=tool_name,
                operation=decision.operation,
                status="error",
                started=started,
                detail={"code": error_payload.get("code")},
            )
            raise MCPToolError(
                error_payload.get("message") or f"{tool_name} reported an error.",
                code=error_payload.get("code") or "MCP_TOOL_ERROR",
                retryable=bool(error_payload.get("retryable")),
                suggested_action=error_payload.get("suggested_action", ""),
                server_id=server_id,
                tool_name=tool_name,
            )

        if writing:
            await self.ledger.complete(key, {"result_keys": sorted(result.data)[:20]})

        await self._audit(
            audit_db,
            run_id=run_id,
            node_id=node_id,
            session_id=session_id,
            server_id=server_id,
            tool_name=tool_name,
            operation=decision.operation,
            status="completed",
            started=started,
            # Shape only, never CRM content — an audit trail must not become a
            # second copy of the customer database (§27).
            detail={
                "result_keys": sorted(result.data)[:20],
                "structured": result.is_structured,
            },
        )

        return {
            "server": server_id,
            "server_label": connection.label,
            "tool": tool_name,
            "operation": decision.operation,
            "data": result.data,
            "text": result.text,
            "is_structured": result.is_structured,
            "result_source": result.source,
            "mode": descriptor["mode"],
            "duration_s": round(duration, 3),
            "deduplicated": False,
            "raw": result.raw,
        }

    def _require_connection(
        self, server_id: str, tool_name: str
    ) -> MCPServerConnection:
        """Internal helper for the require connection step.

        Args:
            server_id (str): The server id.
            tool_name (str): The tool name.

        Returns:
            MCPServerConnection: The connection.
        """
        connection = self.registry.get(server_id)
        if connection is None:
            raise MCPToolError(
                f"MCP server {server_id!r} is not configured. Available: "
                f"{sorted(self.registry.ids) or 'none'}.",
                code="MCP_SERVER_NOT_CONFIGURED",
                retryable=False,
                suggested_action=(
                    "Pick a configured server, or add the connection to the "
                    "deployment."
                ),
                server_id=server_id,
                tool_name=tool_name,
            )
        return connection

    def _replay_or_refuse(
        self,
        existing: dict[str, Any],
        key: str,
        connection: MCPServerConnection,
        tool_name: str,
        decision: PolicyDecision,
    ) -> dict[str, Any]:
        """Internal helper for the replay or refuse step.

        Args:
            existing (dict[str, Any]): The existing.
            key (str): Lookup key.
            connection (MCPServerConnection): The connection.
            tool_name (str): The tool name.
            decision (PolicyDecision): Human decision mapping.

        Returns:
            dict[str, Any]: The or refuse.
        """
        status = existing.get("status")
        if status == "completed":
            log.info("mcp.write_deduplicated", tool=tool_name, key=key)
            return {
                "server": connection.id,
                "server_label": connection.label,
                "tool": tool_name,
                "operation": decision.operation,
                "data": {},
                "text": "",
                "is_structured": False,
                "result_source": "deduplicated",
                "mode": "mock" if connection.is_mock else "live",
                "duration_s": 0.0,
                "deduplicated": True,
                "raw": "",
            }
        raise OperationInFlight(
            f"An identical {tool_name} against {connection.label} is already "
            f"recorded as {status!r}. It may already have changed CRM data — "
            f"check the record before retrying (operation key {key[:12]})."
        )

    async def _audit(
        self,
        audit_db: Any,
        *,
        run_id: str,
        node_id: str,
        session_id: str,
        server_id: str,
        tool_name: str,
        operation: str,
        status: str,
        started: float,
        detail: dict[str, Any],
    ) -> None:
        """Record the invocation (§27).

        Deliberately records *shape*, never CRM records: an audit collection
        full of customer names and phone numbers is a second, less protected
        copy of the CRM.
        """
        if audit_db is None or not run_id:
            return
        try:
            await audit_db["mcp_tool_invocations"].insert_one(
                {
                    "run_id": run_id,
                    "node_id": node_id,
                    "session_id": session_id,
                    "server_id": server_id,
                    "tool_name": tool_name,
                    "operation_class": operation,
                    "status": status,
                    "started_at": started,
                    "completed_at": time.time(),
                    "duration_s": round(time.time() - started, 3),
                    **detail,
                }
            )
        except Exception as error:
            # An audit failure must not fail the workflow; it must be loud.
            log.warning("mcp.audit_failed", error=str(error), tool=tool_name)


def _tool_error_payload(result: MCPToolResult) -> dict[str, Any] | None:
    """Recognise a structured error a server returned in place of a result."""
    if not result.is_error and "error" not in result.data:
        return None
    error = result.data.get("error")
    if isinstance(error, dict):
        return error
    if result.is_error:
        return {
            "code": "MCP_TOOL_ERROR",
            "message": result.text or "The tool reported an error.",
            "retryable": False,
        }
    return None


def _humanise(name: str) -> str:
    """Internal helper for the humanise step.

    Args:
        name (str): Workflow or resource name.

    Returns:
        str: The result.
    """
    return name.replace("-", " ").replace("_", " ").strip().title()
