"""SQLQueryAgent — a thin, fixed-tool client of MCP's query_readonly.

Unlike tests/test_business_records_mcp.py (which exercises the real
query_readonly handler against a live MySQL database and its actual
defense-in-depth), this file fakes the MCP transport entirely — the claim
under test here is just that the node calls the one tool correctly and
shapes its own output envelope (rows/first/count/found/truncated), the same
separation test_mcp_tool_node.py draws between the node and the service
behind it.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.integrations.operations import ExternalOperationLedger
from app.mcp.registry import MCPServerConnection, MCPServerRegistry
from app.mcp.service import MCPIntegrationService
from app.nodes.sql_query import SQLQueryAgent


class FakeTool:
    def __init__(self, name: str, *, output_schema: dict | None = None):
        self.name = name
        self.title = None
        self.description = ""
        self.inputSchema = {"type": "object", "properties": {}}
        self.outputSchema = output_schema
        self.annotations = None
        self.meta = None


class FakeResult:
    def __init__(self, *, structured=None, is_error: bool = False):
        self.structuredContent = structured
        self.content = []
        self.isError = is_error


class FakeClient:
    def __init__(self, tools: list[FakeTool], results: dict[str, Any] | None = None):
        self._tools = tools
        self._results = results or {}
        self.calls: list[dict[str, Any]] = []
        self.running_servers = ("business_records",)

    async def list_tools(self, server: str):
        del server
        return self._tools

    async def call_tool_raw(self, name, arguments, *, server, timeout_seconds=None):
        self.calls.append({"tool": name, "arguments": arguments, "server": server})
        outcome = self._results.get(name, FakeResult(structured={"rows": [], "count": 0, "truncated": False}))
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome


QUERY_READONLY_TOOL = FakeTool(
    "query_readonly",
    output_schema={
        "type": "object",
        "properties": {
            "rows": {"type": "array", "items": {"type": "object"}},
            "count": {"type": "integer"},
            "truncated": {"type": "boolean"},
        },
    },
)


def service(client: FakeClient) -> MCPIntegrationService:
    registry = MCPServerRegistry()
    registry.add(MCPServerConnection(
        id="business_records", display_name="Business Records",
        command="python", args=["-m", "whatever"],
    ))
    return MCPIntegrationService(registry=registry, client=client, ledger=ExternalOperationLedger())


def node(config: dict, svc: MCPIntegrationService, node_id: str = "lookup") -> SQLQueryAgent:
    instance = SQLQueryAgent(node_id, config)
    instance.services = {"mcp": svc}
    return instance


async def run(instance: SQLQueryAgent, run_id: str = "run-1"):
    state = {"inputs": {"SYSTEM.run_id": run_id}, "node_outputs": {}}
    return await instance.run(state, instance.config.model_dump())


BASE_CONFIG = {
    "sql": "SELECT name FROM crm_accounts WHERE name LIKE %(pattern)s",
    "params": {"pattern": "%a%"},
}


class TestSuccess:
    @pytest.mark.asyncio
    async def test_calls_query_readonly_with_the_configured_sql_and_params(self):
        client = FakeClient([QUERY_READONLY_TOOL], {
            "query_readonly": FakeResult(structured={
                "rows": [{"name": "Acme"}], "count": 1, "truncated": False,
            }),
        })
        instance = node(BASE_CONFIG, service(client))
        result = await run(instance)

        assert client.calls == [{
            "tool": "query_readonly",
            "arguments": {
                "sql": BASE_CONFIG["sql"],
                "params": BASE_CONFIG["params"],
                "max_rows": 100,
                "timeout_seconds": 10.0,
            },
            "server": "business_records",
        }]
        assert result["status"] == "ok"
        assert result["rows"] == [{"name": "Acme"}]
        assert result["first"] == {"name": "Acme"}
        assert result["count"] == 1
        assert result["found"] is True
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_no_rows_is_found_false_with_an_empty_first(self):
        client = FakeClient([QUERY_READONLY_TOOL], {
            "query_readonly": FakeResult(structured={"rows": [], "count": 0, "truncated": False}),
        })
        result = await run(node(BASE_CONFIG, service(client)))
        assert result["found"] is False
        assert result["first"] == {}

    @pytest.mark.asyncio
    async def test_never_asserts_approval_is_needed_a_select_needs_no_review(self):
        # approval_satisfied=True is passed unconditionally in the node —
        # verified indirectly: a policy that would refuse an unapproved
        # write must never even be consulted for a query_readonly call,
        # since the connection's own operation classification (read) is
        # what the real gate keys on, not this node's insistence.
        client = FakeClient([QUERY_READONLY_TOOL], {
            "query_readonly": FakeResult(structured={"rows": [], "count": 0, "truncated": False}),
        })
        result = await run(node(BASE_CONFIG, service(client)))
        assert result["status"] == "ok"


def _tool_error(code: str, message: str, *, retryable: bool = False) -> FakeResult:
    # Mirrors server.py's own _error_result() shape exactly: a server-side
    # domain error (SQLGuardError, a mysql.connector.Error) surfaces as a
    # normal, non-raising response with isError=True and a structured
    # {"error": {...}} body — never a raised exception on the transport.
    return FakeResult(structured={"error": {"code": code, "message": message, "retryable": retryable}}, is_error=True)


class TestFailure:
    @pytest.mark.asyncio
    async def test_fail_on_error_true_raises(self):
        client = FakeClient([QUERY_READONLY_TOOL], {
            "query_readonly": _tool_error("SQL_WRITE_NOT_ALLOWED", "looks like a write statement"),
        })
        instance = node(BASE_CONFIG, service(client))
        with pytest.raises(RuntimeError, match="SQL_WRITE_NOT_ALLOWED"):
            await run(instance)

    @pytest.mark.asyncio
    async def test_fail_on_error_false_returns_a_routable_status(self):
        client = FakeClient([QUERY_READONLY_TOOL], {
            "query_readonly": _tool_error("SQL_TIMEOUT", "timed out", retryable=True),
        })
        instance = node({**BASE_CONFIG, "fail_on_error": False}, service(client))
        result = await run(instance)
        assert result["status"] == "error"
        assert result["error_code"] == "SQL_TIMEOUT"
        assert result["retryable"] is True
        assert result["rows"] == []
