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

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from typing import Any, Mapping

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import Settings, settings
from app.mcp.registry import load_servers
from app.observability.logging import get_logger

log = get_logger(__name__)

DEFAULT_SERVER = "eurskem"


def _paper_search_mcp_env(app_settings: Settings) -> dict[str, str]:
    """Research credentials for the paper-search-mcp subprocess.

    Merged onto a copy of the current process environment — pydantic-settings'
    own env_file loading does not populate os.environ, so in local dev these
    values only exist inside `settings`, not anything a child process would
    inherit. Docker's `env_file:` directive does put them in os.environ, but
    passing them explicitly here keeps behavior identical in both cases
    rather than depending on which one happens to be true.

    Names match exactly what paper_search_mcp.config.get_env() looks for
    (its ENV_PREFIX is "PAPER_SEARCH_MCP_"), consumed by
    app/mcp/paper_search_server.py before it hands off to the upstream server.
    """
    env = dict(os.environ)
    env["PAPER_SEARCH_MCP_OPENALEX_API_KEY"] = (
        app_settings.paper_search_mcp_openalex_api_key
    )
    env["PAPER_SEARCH_MCP_UNPAYWALL_EMAIL"] = (
        app_settings.paper_search_mcp_unpaywall_email
    )
    env["PAPER_SEARCH_MCP_CORE_API_KEY"] = (
        app_settings.paper_search_mcp_core_api_key
    )
    env["PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY"] = (
        app_settings.paper_search_mcp_semantic_scholar_api_key
    )
    # Tells our adapter which upstream module to delegate to (default
    # "paper_search_mcp.server") — see app/mcp/paper_search_server.py.
    env["PAPER_SEARCH_MCP_MODULE"] = app_settings.paper_search_mcp_module
    path = app_settings.resolved_paper_search_mcp_path
    if path is not None:
        env["PAPER_SEARCH_MCP_SOURCE_PATH"] = str(path)
    return env


def _dynamics_env(app_settings: Settings) -> dict[str, str]:
    """Environment for the Dynamics 365 MCP subprocess.

    Credentials come from the platform's own settings and are handed to the
    subprocess here. They never appear in workflow YAML, never reach the
    Builder, and never cross the MCP protocol boundary — the workflow references
    the server by id and the server holds the connection.
    """
    env = dict(os.environ)
    env["DYNAMICS_MODE"] = app_settings.dynamics_mcp_mode
    if app_settings.dynamics_url:
        env["DYNAMICS_URL"] = app_settings.dynamics_url
    if app_settings.dynamics_tenant_id:
        env["DYNAMICS_TENANT_ID"] = app_settings.dynamics_tenant_id
    if app_settings.dynamics_client_id:
        env["DYNAMICS_CLIENT_ID"] = app_settings.dynamics_client_id
    if app_settings.dynamics_client_secret:
        env["DYNAMICS_CLIENT_SECRET"] = app_settings.dynamics_client_secret
    if app_settings.dynamics_fixtures_path:
        env["DYNAMICS_FIXTURES"] = app_settings.dynamics_fixtures_path
    return env


def _finance_scm_spec(app_settings: Settings) -> StdioServerParameters:
    """Launch spec for the Dynamics 365 Finance & Operations MCP server.

    Mirrors _dynamics_env's mock/live split: `mock` (default) runs
    app/mcp/d365_finance/server.py, a fixture-backed Python server exposing
    the narrow business tools (find_customer, get_account_ownership, ...)
    this workflow family needs; `live` runs the real Node/TypeScript adapter
    at mcp-servers/d365-finance-scm-mcp against a real F&O tenant. See
    app/mcp/connections.py:finance_scm_connection for the fuller explanation
    of why the two modes aren't tool-for-tool identical yet.
    """
    if app_settings.fno_mcp_mode.strip().lower() != "live":
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp.d365_finance.server"],
        )

    from pathlib import Path

    entrypoint = (
        Path(__file__).resolve().parents[2]
        / "mcp-servers" / "d365-finance-scm-mcp" / "dist" / "src" / "index.js"
    )
    env = dict(os.environ)
    env["FNO_BASE_URL"] = app_settings.fno_base_url
    if app_settings.fno_tenant_id:
        env["FNO_TENANT_ID"] = app_settings.fno_tenant_id
    if app_settings.fno_client_id:
        env["FNO_CLIENT_ID"] = app_settings.fno_client_id
    if app_settings.fno_client_secret:
        env["FNO_CLIENT_SECRET"] = app_settings.fno_client_secret
    env["FNO_ALLOW_WRITES"] = "true" if app_settings.fno_allow_writes else "false"
    env["FNO_ALLOW_DELETES"] = "true" if app_settings.fno_allow_deletes else "false"
    env["FNO_READ_ENTITY_ALLOWLIST"] = app_settings.fno_read_entity_allowlist
    env["FNO_WRITE_ENTITY_ALLOWLIST"] = app_settings.fno_write_entity_allowlist
    env["FNO_DELETE_ENTITY_ALLOWLIST"] = app_settings.fno_delete_entity_allowlist
    env["FNO_ENTITY_ALIASES_JSON"] = app_settings.fno_entity_aliases_json
    return StdioServerParameters(command="node", args=[str(entrypoint)], env=env)


def _business_records_env(app_settings: Settings) -> dict[str, str]:
    """Environment for the Business Records MCP subprocess.

    Connection details come from the platform's own settings — a live MySQL
    database, unlike the fixture-backed mocks above — and are handed to the
    subprocess the same way Dynamics credentials are, never through workflow
    YAML or the MCP protocol boundary.
    """
    env = dict(os.environ)
    env["BUSINESS_RECORDS_MYSQL_HOST"] = app_settings.business_records_mysql_host
    env["BUSINESS_RECORDS_MYSQL_PORT"] = str(app_settings.business_records_mysql_port)
    env["BUSINESS_RECORDS_MYSQL_USER"] = app_settings.business_records_mysql_user
    env["BUSINESS_RECORDS_MYSQL_PASSWORD"] = app_settings.business_records_mysql_password
    env["BUSINESS_RECORDS_MYSQL_DATABASE"] = app_settings.business_records_mysql_database
    return env


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

    if app_settings.dynamics_mcp_enabled:
        specs["dynamics365"] = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp.dynamics.server"],
            env=_dynamics_env(app_settings),
        )

    if app_settings.fno_mcp_enabled:
        specs["dynamics365_finance_scm"] = _finance_scm_spec(app_settings)

    if app_settings.business_records_mcp_enabled:
        specs["business_records"] = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp.business_records.server"],
            env=_business_records_env(app_settings),
        )

    # Servers declared through MCP_SERVERS join the same launch table, so a
    # third-party MCP server is configured exactly like a first-party one and
    # gets the same policy, timeout and audit treatment.
    for connection in load_servers().values():
        if connection.id in specs:
            log.warning(
                "mcp.client.duplicate_server_id",
                server=connection.id,
                detail="a configured server shadows a built-in one; built-in kept",
            )
            continue
        specs[connection.id] = StdioServerParameters(
            command=connection.command,
            args=list(connection.args),
            env={**os.environ, **connection.resolve_environment()},
        )

    if not app_settings.paper_search_mcp_enabled:
        return specs

    # Always launch OUR adapter (app/mcp/paper_search_server.py), never the
    # upstream module directly — it injects the OpenAlex api_key into the
    # searcher's session before delegating to paper_search_mcp.server.main(),
    # and that injection only runs if it is the actual entry point.
    command = app_settings.paper_search_mcp_command.strip() or sys.executable
    if command == "python":
        command = sys.executable

    specs["paper-search-mcp"] = StdioServerParameters(
        command=command,
        args=["-m", "app.mcp.paper_search_server"],
        env=_paper_search_mcp_env(app_settings),
    )
    return specs


class MCPClient:
    """Multi-server MCP wrapper. call_tool(name, arguments, server=...) routes
    to the named server; omitting `server` uses the primary (back-compat)."""

    def __init__(
        self,
        servers: Mapping[str, StdioServerParameters] | None = None,
        *,
        startup_timeout_seconds: float | None = None,
        tool_timeout_seconds: float | None = None,
    ):
        self._specs = dict(servers) if servers is not None else build_server_specs()
        self._stacks: dict[str, AsyncExitStack] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._tools_cache: dict[str, list] = {}
        self._startup_timeout_seconds = (
            startup_timeout_seconds
            if startup_timeout_seconds is not None
            else settings.mcp_startup_timeout_seconds
        )
        self._tool_timeout_seconds = (
            tool_timeout_seconds
            if tool_timeout_seconds is not None
            else settings.mcp_tool_timeout_seconds
        )
        self._stopped = False

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
            if name in self._sessions:
                continue
            spec = self._specs.get(name)
            if spec is None:
                log.warning("mcp.client.unknown_server", server=name)
                continue
            stack = AsyncExitStack()
            try:
                async with asyncio.timeout(self._startup_timeout_seconds):
                    read, write = await stack.enter_async_context(
                        stdio_client(spec)
                    )
                    session = await stack.enter_async_context(
                        ClientSession(read, write)
                    )
                    await session.initialize()
                self._stacks[name] = stack
                self._sessions[name] = session
                log.info("mcp.client.started", server=name)
            except Exception as exc:
                # A partially opened stdio/session stack must be closed now.
                # Retaining it makes a degraded startup crash again on shutdown.
                try:
                    await stack.aclose()
                except Exception as cleanup_exc:
                    log.warning(
                        "mcp.client.start_cleanup_failed",
                        server=name,
                        error_type=type(cleanup_exc).__name__,
                    )
                log.warning(
                    "mcp.client.start_failed",
                    server=name,
                    error_type=type(exc).__name__,
                )

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        for name, stack in reversed(tuple(self._stacks.items())):
            try:
                await stack.aclose()
            except Exception as exc:
                log.warning(
                    "mcp.client.stop_failed",
                    server=name,
                    error_type=type(exc).__name__,
                )
        self._stacks.clear()
        self._sessions.clear()
        self._tools_cache.clear()
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
            async with asyncio.timeout(self._tool_timeout_seconds):
                resp = await self._require(server).list_tools()
            self._tools_cache[server] = resp.tools
        return self._tools_cache[server]

    async def probe(self, server: str = DEFAULT_SERVER) -> bool:
        """Perform a live MCP request instead of trusting a cached session."""

        async with asyncio.timeout(self._tool_timeout_seconds):
            resp = await self._require(server).list_tools()
        self._tools_cache[server] = resp.tools
        return True

    async def call_tool(
        self, name: str, arguments: dict, server: str = DEFAULT_SERVER
    ) -> Any:
        """Call a tool on `server` (default: primary). Returns the first
        TextContent's text — identical unwrap to the original client, so
        existing callers get byte-identical behaviour."""
        resp = await self.call_tool_raw(name, arguments, server=server)
        if resp.content and hasattr(resp.content[0], "text"):
            return resp.content[0].text
        return ""

    async def call_tool_raw(
        self,
        name: str,
        arguments: dict,
        *,
        server: str = DEFAULT_SERVER,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Call a tool and return the full CallToolResult.

        Exists because `call_tool` above throws away everything except the first
        text block — which was fine when every server serialised its answer into
        text, and is exactly the lossy step to avoid now that servers can return
        `structuredContent`. Existing callers keep the old unwrap; the MCP Tool
        node uses this one and normalises through app/mcp/results.py.
        """
        limit = timeout_seconds or self._tool_timeout_seconds
        try:
            async with asyncio.timeout(limit):
                return await self._require(server).call_tool(name, arguments)
        except TimeoutError as exc:
            raise TimeoutError(
                f"MCP tool '{server}.{name}' exceeded {limit:g} seconds"
            ) from exc


async def launch_mcp_session(servers: list[str] | None = None) -> MCPClient:
    """Convenience used by app/main.py at FastAPI startup.

    By default launches all declared servers. To keep the primary-only
    behaviour during rollout, call launch_mcp_session(["eurskem"]).
    """
    client = MCPClient()
    await client.start(servers)
    return client
