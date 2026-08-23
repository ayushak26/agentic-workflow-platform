"""Fixture-backed mock for the Dynamics 365 Finance & Operations MCP server.

Runs as a stdio subprocess, same shape as app/mcp/dynamics/server.py (the
Dataverse CRM connector) — a generic MCP Tool node talks to the Eurskem MCP
client, which talks to this server, which serves fixture data.

This is NOT the real mcp-servers/d365-finance-scm-mcp server (that one is a
Node/TypeScript adapter over the real F&O OData API and has no mock mode of
its own — see its README). This module exists so a workflow that names
find_customer / get_account_ownership / get_quote / get_sales_order /
get_order_fulfilment_status / get_credit_status / get_inventory_availability
can be built and demonstrated today, without a live F&O tenant. Wiring the
real server means building that same narrow tool vocabulary as a layer over
its generic erp_query/erp_get_record tools — its README says as much — at
which point this fixture server and that layer should expose identical tool
contracts, the same relationship the CRM connector's mock and live backends
already have.
"""
from __future__ import annotations

import sys
import logging

logging.basicConfig(stream=sys.stderr, force=True)

from app.observability.logging import configure_logging  # noqa: E402

configure_logging()

import asyncio  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import mcp.types as types  # noqa: E402
from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402

from app.mcp.dynamics.client import DynamicsBackend, DynamicsError, FixtureBackend  # noqa: E402
from app.mcp.d365_finance.handlers import HANDLERS  # noqa: E402
from app.mcp.d365_finance.tools import TOOL_DEFINITIONS, TOOLS_BY_NAME  # noqa: E402
from app.observability.logging import get_logger  # noqa: E402

log = get_logger(__name__)

server: Server = Server("eurskem-dynamics365-finance-scm")

DEFAULT_FIXTURES = Path(__file__).with_name("fixtures.json")

_backend: DynamicsBackend | None = None


def backend() -> DynamicsBackend:
    """Compute the backend.

    Returns:
        DynamicsBackend: The result.
    """
    global _backend
    if _backend is None:
        log.info("d365_finance.backend.mock", fixtures=str(DEFAULT_FIXTURES))
        _backend = FixtureBackend.from_file(DEFAULT_FIXTURES)
    return _backend


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """List the tools.

    Returns:
        list[types.Tool]: The tools.
    """
    return [
        types.Tool(
            name=definition["name"],
            title=definition.get("title"),
            description=definition["description"],
            inputSchema=definition["input_schema"],
            outputSchema=definition["output_schema"],
            annotations=types.ToolAnnotations(
                title=definition.get("title"),
                readOnlyHint=True,
                openWorldHint=True,
            ),
            meta={
                "eurskem": {
                    "operation": definition["operation"],
                    "system": "Microsoft Dynamics 365 Finance & Operations",
                    "typical_uses": definition.get("typical_uses", []),
                    "mode": "mock",
                }
            },
        )
        for definition in TOOL_DEFINITIONS
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
    """Compute the call tool.

    Args:
        name (str): Workflow or resource name.
        arguments (dict[str, Any] | None): The arguments.

    Returns:
        Any: The tool.
    """
    definition = TOOLS_BY_NAME.get(name)
    handler = HANDLERS.get(name)
    if definition is None or handler is None:
        return _error_result(
            {
                "code": "MCP_UNKNOWN_TOOL",
                "message": f"Unknown tool {name!r}. Available: {sorted(TOOLS_BY_NAME)}.",
                "retryable": False,
                "suggested_action": "Pick a tool from the discovered list.",
            }
        )

    args = dict(arguments or {})
    args.pop("session_id", None)

    try:
        payload = await handler(backend(), args)
    except DynamicsError as error:
        log.warning("d365_finance.tool_failed", tool=name, code=error.code)
        return _error_result(error.as_payload())
    except Exception as error:  # unexpected — still must not crash the session
        log.error("d365_finance.tool_crashed", tool=name, error=str(error), exc_info=True)
        return _error_result(
            {
                "code": "D365_FINANCE_UNEXPECTED_ERROR",
                "message": f"{type(error).__name__}: {error}",
                "retryable": False,
                "suggested_action": "Report this diagnostic.",
            }
        )

    enriched = {**payload, "_mode": "mock"}
    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text=json.dumps(enriched, ensure_ascii=False, default=str)),
        ],
        structuredContent=enriched,
    )


def _error_result(error: dict[str, Any]) -> types.CallToolResult:
    """Internal helper for the error result step.

    Args:
        error (dict[str, Any]): Error value or message.

    Returns:
        types.CallToolResult: The result.
    """
    body = {"error": error}
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
        structuredContent=body,
        isError=True,
    )


async def main() -> None:
    """Compute the main."""
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
