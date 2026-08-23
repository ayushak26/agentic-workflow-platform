"""The Dynamics 365 MCP server.

Runs as a stdio subprocess launched by the Eurskem MCP client. It exposes CRM
capabilities as MCP tools and knows nothing about workflows; the workflow knows
nothing about the Dataverse Web API. That is the whole point of the boundary.

    Generic MCP Tool node
            ↓
    Eurskem MCP client
            ↓
    THIS SERVER
            ↓
    Dataverse Web API  ──or──  fixture store
            ↓
    Microsoft Entra ID

Mode is chosen by `DYNAMICS_MODE`:

    live   real tenant, client-credentials auth
    mock   the same tool contracts over fixtures (§22)

Both modes serve identical tool names, input schemas and output schemas, so a
workflow built against the demo runs unchanged against production.

Results are returned as `structuredContent` with a declared `outputSchema`, not
as a JSON string in a text block — so downstream mapping works on typed fields
rather than on text somebody has to parse (§14).
"""
from __future__ import annotations

import sys
import logging

logging.basicConfig(stream=sys.stderr, force=True)

# Route logging to stderr before anything else can write to stdout: stdout is
# the MCP JSON-RPC stream, and a single stray print corrupts the session.
from app.observability.logging import configure_logging  # noqa: E402

configure_logging()

import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import mcp.types as types  # noqa: E402
from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402

from app.mcp.dynamics.client import (  # noqa: E402
    DataverseClient,
    DynamicsBackend,
    DynamicsError,
    FixtureBackend,
)
from app.mcp.dynamics.handlers import HANDLERS  # noqa: E402
from app.mcp.dynamics.odata import ODataValueError  # noqa: E402
from app.mcp.dynamics.tools import TOOL_DEFINITIONS, TOOLS_BY_NAME  # noqa: E402
from app.observability.logging import get_logger  # noqa: E402

log = get_logger(__name__)

server: Server = Server("eurskem-dynamics365")

DEFAULT_FIXTURES = Path(__file__).with_name("fixtures.json")

_backend: DynamicsBackend | None = None


def build_backend() -> DynamicsBackend:
    """Choose a backend from the environment.

    Mock is the default. A misconfigured live connection should surface as an
    obvious demo backend rather than as a half-working production one — and
    every response says which mode produced it, so the two are never confused.
    """
    mode = os.environ.get("DYNAMICS_MODE", "mock").strip().lower()
    if mode != "live":
        path = os.environ.get("DYNAMICS_FIXTURES", "").strip()
        fixtures = Path(path) if path else DEFAULT_FIXTURES
        log.info("dynamics.backend.mock", fixtures=str(fixtures))
        return FixtureBackend.from_file(fixtures)

    base_url = os.environ.get("DYNAMICS_URL", "").strip()
    if not base_url:
        raise RuntimeError(
            "DYNAMICS_MODE=live requires DYNAMICS_URL "
            "(e.g. https://your-org.crm.dynamics.com)"
        )
    log.info("dynamics.backend.live", url=base_url)
    return DataverseClient(
        base_url=base_url,
        tenant_id=os.environ.get("DYNAMICS_TENANT_ID", "").strip(),
        client_id=os.environ.get("DYNAMICS_CLIENT_ID", "").strip(),
        client_secret=os.environ.get("DYNAMICS_CLIENT_SECRET", "").strip(),
    )


def backend() -> DynamicsBackend:
    """Compute the backend.

    Returns:
        DynamicsBackend: The result.
    """
    global _backend
    if _backend is None:
        _backend = build_backend()
    return _backend


def _annotations(definition: dict[str, Any]) -> types.ToolAnnotations:
    """Declare the tool's nature honestly.

    These are hints for any MCP client. The Eurskem policy layer deliberately
    does not trust them for a safety decision — a server should not be the sole
    authority on whether calling it is dangerous — but declaring them correctly
    is still the right thing for every other client.
    """
    operation = definition["operation"]
    read_only = operation == "read"
    return types.ToolAnnotations(
        title=definition.get("title"),
        readOnlyHint=read_only,
        destructiveHint=operation == "destructive",
        # Reads repeat safely. `update_*` is idempotent in the CRM sense —
        # applying it twice leaves the same state — but `create_*` is not, and
        # saying so is what lets a caller decide whether a retry is safe.
        idempotentHint=read_only or definition["name"].startswith("update_"),
        openWorldHint=True,
    )


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
            annotations=_annotations(definition),
            meta={
                "eurskem": {
                    "operation": definition["operation"],
                    "system": "Microsoft Dynamics 365",
                    "typical_uses": definition.get("typical_uses", []),
                    "mode": "mock" if backend().is_mock else "live",
                }
            },
        )
        for definition in TOOL_DEFINITIONS
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
    """Execute one tool.

    Errors are returned as structured payloads rather than raised as opaque
    strings: a workflow should be able to route on "the customer is not in the
    CRM" without pattern-matching an error message (§28).
    """
    definition = TOOLS_BY_NAME.get(name)
    handler = HANDLERS.get(name)
    if definition is None or handler is None:
        return _error_result(
            {
                "code": "MCP_UNKNOWN_TOOL",
                "message": (
                    f"Unknown tool {name!r}. Available: {sorted(TOOLS_BY_NAME)}."
                ),
                "retryable": False,
                "suggested_action": "Pick a tool from the discovered list.",
            }
        )

    args = dict(arguments or {})
    # The workflow runtime threads a session id through every MCP call; it is
    # not a CRM argument and must not reach a query.
    args.pop("session_id", None)

    try:
        payload = await handler(backend(), args)
    except ODataValueError as error:
        # A rejected identifier or over-long term is a *caller* error with a
        # specific fix, not an internal fault — and it is the shape an injection
        # attempt arrives in, so it gets its own code rather than being reported
        # as "something unexpected happened".
        log.warning("dynamics.invalid_argument", tool=name, reason=str(error)[:200])
        return _error_result(
            {
                "code": "CRM_INVALID_ARGUMENTS",
                "message": str(error),
                "retryable": False,
                "suggested_action": (
                    "Map this value from a previous CRM lookup rather than "
                    "typing or generating it."
                ),
            }
        )
    except DynamicsError as error:
        log.warning("dynamics.tool_failed", tool=name, code=error.code)
        return _error_result(error.as_payload())
    except Exception as error:  # unexpected — still must not crash the session
        log.error("dynamics.tool_crashed", tool=name, error=str(error), exc_info=True)
        return _error_result(
            {
                "code": "DYNAMICS_UNEXPECTED_ERROR",
                "message": f"{type(error).__name__}: {error}",
                "retryable": False,
                "suggested_action": "Report this diagnostic.",
            }
        )

    enriched = {**payload, "_mode": "mock" if backend().is_mock else "live"}
    # Both forms: structuredContent for typed consumers, text for MCP clients
    # (and models) that only read content blocks.
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=json.dumps(enriched, ensure_ascii=False, default=str)
            )
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
        content=[
            types.TextContent(
                type="text", text=json.dumps(body, ensure_ascii=False)
            )
        ],
        structuredContent=body,
        isError=True,
    )


async def main() -> None:
    """Compute the main."""
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
