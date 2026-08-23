"""SQLQueryAgent — a read-only SQL lookup, for a query the classified MCP
tools don't already cover.

Deliberately not its own connection or execution path — a SQL query opening
a blocking `mysql.connector` call directly on this process's event loop
would freeze the whole application the same way an in-process CPU-bound
snippet would (see app/nodes/python_snippet.py's own module docstring for
that exact, separately-verified failure mode). Instead this is a thin client
of the `query_readonly` tool already registered on the `business_records` MCP
server (app/mcp/business_records/tools.py), which already gives process
isolation via the MCP stdio transport — this node is structurally close to
app/nodes/mcp_tool.py, fixed to one tool rather than letting the author pick
any tool on any server.

Every real safety property (read-only credential, read-only transaction,
row/timeout limits, write-verb rejection) lives in
app/mcp/business_records/sql_guard.py, on the server side of the MCP
boundary — this node has no write mode, at all, full stop. A business write
belongs to one of the classified `create_*`/`update_*` tools, not to SQL
text a workflow author can edit.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.mcp.service import MCPToolError
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger

log = get_logger(__name__)


class SQLQueryConfig(BaseModel):
    """Pydantic model defining the SQLQueryConfig shape.

    Attributes:
        server_id (str).
        sql (str).
        params (dict[str, Any]).
        max_rows (int).
        timeout_seconds (float).
        fail_on_error (bool).
    """
    model_config = ConfigDict(extra="forbid")

    #: Fixed to the one SQL-capable connection in this deployment today —
    #: same "never a URL, never a credential" principle as MCPToolConfig's
    #: own server_id, just with nothing else to choose from yet.
    server_id: str = Field(
        default="business_records",
        description="Which configured MCP connection exposes query_readonly.",
    )
    sql: str = Field(
        description=(
            "A single SELECT statement. Use %(name)s placeholders for any "
            "mapped value — never build the string with the value already "
            "inside it, and never map a value directly into this field "
            "(preflight rejects a literal {{ here)."
        ),
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Named values for the query's %(name)s placeholders.",
    )
    max_rows: int = Field(default=100, ge=1, le=500)
    timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    #: Same escape hatch every other lookup node offers — "not found" is
    #: often a business fact, not a crash.
    fail_on_error: bool = Field(
        default=True,
        description="When off, a query failure becomes a routable status instead of stopping the run.",
    )


class SQLQueryInput(BaseModel):
    """Pydantic model defining the SQLQueryInput shape."""
    pass


class SQLQueryOutput(BaseModel):
    """Pydantic model defining the SQLQueryOutput shape.

    Attributes:
        status (str).
        rows (list[dict[str, Any]]).
        count (int).
        found (bool).
        first (dict[str, Any]).
        truncated (bool).
        error (str | None).
        error_code (str | None).
    """
    status: str = "ok"  # ok | error
    rows: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    found: bool = False
    first: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False


@NodeRegistry.register
class SQLQueryAgent(NodeType):
    """Workflow node type implementing the SQLQueryAgent capability.

    Attributes:
        family (ClassVar[str]).
        execution_kind (ClassVar[str]).
        about (ClassVar[dict[str, Any]]).
    """
    type_name = "SQLQueryAgent"
    description = (
        "Run a read-only SQL query against the business-records database, "
        "for a lookup the classified MCP tools don't already cover."
    )
    input_schema = SQLQueryInput
    output_schema = SQLQueryOutput
    config_schema = SQLQueryConfig

    family: ClassVar[str] = "specialized"
    execution_kind: ClassVar[str] = "external"
    about: ClassVar[dict[str, Any]] = {
        "what": "Runs one read-only SQL SELECT and returns the matched rows.",
        "why": (
            "The classified lookups (Customer Search, Order Search, ...) "
            "cover the common cases; this is the escape hatch for a "
            "specific query they don't — still read-only, still bounded, "
            "never a way to write."
        ),
        "receives": "SQL text with %(name)s placeholders, and the values for them.",
        "produces": "rows (every matched record), first, count, found, truncated.",
        "uses_ai": False,
        "external_action": True,
        "safety": (
            "Runs under a SELECT-only database credential in a read-only "
            "transaction, with a row limit and a hard timeout. A write "
            "statement is refused before it ever reaches the database."
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
        return {"mcp"}

    @classmethod
    def preflight_output_fields(cls, config: dict[str, Any]) -> set[str]:
        # `rows`/`first` hold whatever columns the author's own SELECT list
        # names — never knowable statically (unlike a classified tool's
        # fixed output_schema), so only the fixed envelope is authorised.
        """Compute the preflight output fields.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The output fields.
        """
        return set(SQLQueryOutput.model_fields)

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = SQLQueryConfig(**resolved_config)
        service = self.services.get("mcp")
        if service is None:
            raise RuntimeError(
                f"SQLQueryAgent '{self.node_id}' needs the MCP integration "
                "service. No MCP server is configured in this deployment."
            )

        inputs = state.get("inputs") or {}
        run_id = str(inputs.get("SYSTEM.run_id") or "")
        session_id = str(state.get("session_id") or "")

        try:
            result = await service.call(
                server_id=cfg.server_id,
                tool_name="query_readonly",
                arguments={
                    "sql": cfg.sql,
                    "params": cfg.params,
                    "max_rows": cfg.max_rows,
                    "timeout_seconds": cfg.timeout_seconds,
                },
                run_id=run_id,
                node_id=self.node_id,
                session_id=session_id,
                # A SELECT can never need human review — there is nothing
                # for a person to approve before a read.
                approval_satisfied=True,
                audit_db=self.services.get("audit_db"),
                timeout_override=cfg.timeout_seconds + 5,
            )
        except MCPToolError as error:
            return self._failure(cfg, error)

        data = result["data"]
        rows = data.get("rows") or []
        return {
            "status": "ok",
            "rows": rows,
            "count": int(data.get("count") or 0),
            "found": bool(rows),
            "first": rows[0] if rows else {},
            "truncated": bool(data.get("truncated")),
            "error": None,
            "error_code": None,
            "retryable": False,
        }

    def _failure(self, cfg: SQLQueryConfig, error: MCPToolError) -> dict[str, Any]:
        """Internal helper for the failure step.

        Args:
            cfg (SQLQueryConfig): The cfg.
            error (MCPToolError): Error value or message.

        Returns:
            dict[str, Any]: The result.
        """
        code = getattr(error, "code", None) or type(error).__name__
        log.warning("sql_query.failed", node_id=self.node_id, code=code)

        if cfg.fail_on_error:
            raise RuntimeError(
                f"SQLQueryAgent '{self.node_id}' failed ({code}): {error}"
            ) from error

        return {
            "status": "error",
            "rows": [],
            "count": 0,
            "found": False,
            "first": {},
            "truncated": False,
            "error": str(error)[:800],
            "error_code": code,
            "retryable": bool(getattr(error, "retryable", False)),
        }
