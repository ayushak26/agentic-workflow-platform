"""Business Records MCP server — a real, persistent MySQL-backed connector.

Runs as a stdio subprocess, same shape as app/mcp/d365_finance/server.py and
app/mcp/dynamics/server.py, except its "backend" is a live MySQL connection
(seeded from both fixture files via seed.py) rather than an in-memory fixture
file. Exposes 10 explicitly classified tools (5 read, 5 write) — never a raw,
unclassified SQL executor: query_readonly is read-only by construction (a
SELECT-only credential, a read-only transaction, a timeout — see
app/mcp/business_records/sql_guard.py), not merely by convention.
"""
from __future__ import annotations

import sys
import logging

logging.basicConfig(stream=sys.stderr, force=True)

from app.observability.logging import configure_logging  # noqa: E402

configure_logging()

import asyncio  # noqa: E402
import json  # noqa: E402
from typing import Any  # noqa: E402

import mcp.types as types  # noqa: E402
import mysql.connector  # noqa: E402
from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mysql.connector.connection import MySQLConnectionAbstract  # noqa: E402

from app.mcp.business_records.db import connect  # noqa: E402
from app.mcp.business_records.handlers import HANDLERS  # noqa: E402
from app.mcp.business_records.sql_guard import SQLGuardError  # noqa: E402
from app.mcp.business_records.tools import TOOL_DEFINITIONS, TOOLS_BY_NAME  # noqa: E402
from app.observability.logging import get_logger  # noqa: E402

log = get_logger(__name__)

server: Server = Server("eurskem-business-records")

_conn: MySQLConnectionAbstract | None = None


def backend() -> MySQLConnectionAbstract:
    global _conn
    if _conn is None or not _conn.is_connected():
        log.info("business_records.backend.connect")
        _conn = connect()
    return _conn


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=definition["name"],
            description=definition["description"],
            inputSchema=definition["input_schema"],
            outputSchema=definition["output_schema"],
            annotations=types.ToolAnnotations(
                readOnlyHint=definition["operation"] == "read",
                openWorldHint=True,
            ),
            meta={
                "eurskem": {
                    "operation": definition["operation"],
                    "system": "Business Records (MySQL)",
                    "mode": "live",
                }
            },
        )
        for definition in TOOL_DEFINITIONS
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
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
    except SQLGuardError as error:
        # query_readonly's own guard (a write verb, a timed-out query) —
        # reported with its real code rather than falling into the generic
        # unexpected-error branch, so a workflow author sees "this looks
        # like a write statement" instead of an opaque crash message.
        return _error_result(
            {
                "code": error.code,
                "message": str(error),
                "retryable": error.code == "SQL_TIMEOUT",
                "suggested_action": (
                    "Rewrite this as a single SELECT statement."
                    if error.code == "SQL_WRITE_NOT_ALLOWED"
                    else "Narrow the query or raise timeout_seconds."
                ),
            }
        )
    except mysql.connector.Error as error:
        log.error("business_records.db_error", tool=name, error=str(error), exc_info=True)
        return _error_result(
            {
                "code": "BUSINESS_RECORDS_DB_ERROR",
                "message": f"{type(error).__name__}: {error}",
                "retryable": True,
                "suggested_action": "Retry; if this persists, check the MySQL container is running.",
            }
        )
    except Exception as error:  # unexpected — still must not crash the session
        log.error("business_records.tool_crashed", tool=name, error=str(error), exc_info=True)
        return _error_result(
            {
                "code": "BUSINESS_RECORDS_UNEXPECTED_ERROR",
                "message": f"{type(error).__name__}: {error}",
                "retryable": False,
                "suggested_action": "Report this diagnostic.",
            }
        )

    enriched = {**payload, "_mode": "live"}
    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text=json.dumps(enriched, ensure_ascii=False, default=str)),
        ],
        structuredContent=enriched,
    )


def _error_result(error: dict[str, Any]) -> types.CallToolResult:
    body = {"error": error}
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
        structuredContent=body,
        isError=True,
    )


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
