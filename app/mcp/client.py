"""MCP client wrapper that launches the server subprocess and keeps a
session open. FastAPI calls launch_mcp_session() at startup and stores
the returned wrapper in app.state.services['mcp_client']."""
from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.observability.logging import get_logger

log = get_logger(__name__)


class MCPClient:
    """Thin wrapper exposing list_tools() and call_tool() to the MCPAgent.

    Lifecycle is managed via an AsyncExitStack so we can cleanly tear down
    the subprocess when FastAPI shuts down."""

    def __init__(self):
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._tools_cache: list | None = None

    async def start(self) -> None:
        # Launch the server as a Python subprocess in the same venv
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "app.mcp.server"]
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        log.info("mcp.client.started")

    async def stop(self) -> None:
        await self._stack.aclose()
        log.info("mcp.client.stopped")

    async def list_tools(self) -> list:
        if self._tools_cache is None:
            resp = await self._session.list_tools()
            self._tools_cache = resp.tools
        return self._tools_cache

    async def call_tool(self, name: str, arguments: dict) -> Any:
        resp = await self._session.call_tool(name, arguments)
        # Tool returns a list of content items; we expect a single TextContent
        if resp.content and hasattr(resp.content[0], "text"):
            return resp.content[0].text
        return ""


async def launch_mcp_session() -> MCPClient:
    """Convenience used by app/main.py at FastAPI startup."""
    client = MCPClient()
    await client.start()
    return client