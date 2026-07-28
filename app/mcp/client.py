"""MCP client wrapper — multi-server.

Launches one or more MCP server subprocesses and keeps a session open per
server for the process lifetime. FastAPI calls launch_mcp_session() at startup
and stores the wrapper in app.state.services['mcp_client'].

Backward compatibility (the important part)
-------------------------------------------
The original client was single-server with call_tool(name, arguments). Every
existing caller (MCPAgent, validate_citation flows, search_web) uses that exact
signature. This refactor keeps it working: `server` is an OPTIONAL keyword that
defaults to the primary server. Old calls route to the primary and behave
identically. Only new callers (EvidenceAgent) pass server="paper-search-mcp".

Design
------
- SERVERS declares the launch spec per server name. The in-repo Eurskem RAG
  server is the DEFAULT_SERVER, so unqualified calls keep hitting it.
- Each server gets its own AsyncExitStack-managed ClientSession, keyed by name.
- One server failing to start does not prevent the others (a missing local
  paper-search-mcp clone must not take down document retrieval).
"""
from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from typing import Any, Mapping

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import Settings, settings
from app.observability.logging import get_logger

log = get_logger(__name__)

DEFAULT_SERVER = "eurskem"

def build_server_specs(
    app_settings: Settings = settings,
) -> dict[str, StdioServerParameters]:
    """Build portable MCP launch specs from application configuration."""

    specs = {
        "eurskem": StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp.server"],
        )
    }
    if not app_settings.paper_search_mcp_enabled:
        return specs

    path = app_settings.resolved_paper_search_mcp_path
    if path is None:
        log.warning(
            "mcp.client.paper_search_disabled",
            reason="PAPER_SEARCH_MCP_PATH is empty",
        )
        return specs

    specs["paper-search-mcp"] = StdioServerParameters(
        command=app_settings.paper_search_mcp_command,
        args=[
            "run",
            "--directory",
            str(path),
            "-m",
            app_settings.paper_search_mcp_module,
        ],
    )
    return specs


class MCPClient:
    """Multi-server MCP wrapper. call_tool(name, arguments, server=...) routes
    to the named server; omitting `server` uses the primary (back-compat)."""

    def __init__(
        self,
        servers: Mapping[str, StdioServerParameters] | None = None,
    ):
        self._specs = dict(servers) if servers is not None else build_server_specs()
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tools_cache: dict[str, list] = {}

    @property
    def configured_servers(self) -> tuple[str, ...]:
        return tuple(self._specs)

    @property
    def running_servers(self) -> tuple[str, ...]:
        return tuple(self._sessions)

    async def start(self, servers: list[str] | None = None) -> None:
        """Launch the requested servers (default: all declared). A server that
        fails to start is logged and skipped, not fatal."""
        names = servers if servers is not None else list(self._specs)
        for name in names:
            spec = self._specs.get(name)
            if spec is None:
                log.warning("mcp.client.unknown_server", server=name)
                continue
            try:
                read, write = await self._stack.enter_async_context(stdio_client(spec))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._sessions[name] = session
                log.info("mcp.client.started", server=name)
            except Exception as exc:
                # e.g. paper-search-mcp clone missing — degrade, don't crash.
                log.warning("mcp.client.start_failed", server=name, error=str(exc))

    async def stop(self) -> None:
        await self._stack.aclose()
        log.info("mcp.client.stopped")

    def _require(self, server: str) -> ClientSession:
        session = self._sessions.get(server)
        if session is None:
            raise RuntimeError(
                f"MCP server '{server}' is not running "
                f"(available: {sorted(self._sessions)})"
            )
        return session

    def has_server(self, server: str) -> bool:
        return server in self._sessions

    async def list_tools(self, server: str = DEFAULT_SERVER) -> list:
        if server not in self._tools_cache:
            resp = await self._require(server).list_tools()
            self._tools_cache[server] = resp.tools
        return self._tools_cache[server]

    async def probe(self, server: str = DEFAULT_SERVER) -> bool:
        """Perform a live MCP request instead of trusting a cached session."""

        resp = await self._require(server).list_tools()
        self._tools_cache[server] = resp.tools
        return True

    async def call_tool(
        self, name: str, arguments: dict, server: str = DEFAULT_SERVER
    ) -> Any:
        """Call a tool on `server` (default: primary). Returns the first
        TextContent's text — identical unwrap to the original client, so
        existing callers get byte-identical behaviour."""
        resp = await self._require(server).call_tool(name, arguments)
        if resp.content and hasattr(resp.content[0], "text"):
            return resp.content[0].text
        return ""


async def launch_mcp_session(servers: list[str] | None = None) -> MCPClient:
    """Convenience used by app/main.py at FastAPI startup.

    By default launches all declared servers. To keep the primary-only
    behaviour during rollout, call launch_mcp_session(["eurskem"]).
    """
    client = MCPClient()
    await client.start(servers)
    return client
