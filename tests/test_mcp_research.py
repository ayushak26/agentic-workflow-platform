from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.mcp.client import MCPClient, build_server_specs


def test_installed_paper_search_uses_current_python_without_source_path():
    specs = build_server_specs(
        Settings(
            _env_file=None,
            paper_search_mcp_enabled=True,
            paper_search_mcp_path="",
            paper_search_mcp_command="python",
        )
    )
    spec = specs["paper-search-mcp"]
    # Always launches OUR adapter (app/mcp/paper_search_server.py), never the
    # upstream module directly — only the adapter injects the OpenAlex
    # api_key before handing off. It reads which upstream module to delegate
    # to from PAPER_SEARCH_MCP_MODULE in its own environment.
    assert spec.args == ["-m", "app.mcp.paper_search_server"]
    assert "python" in spec.command
    assert spec.env["PAPER_SEARCH_MCP_MODULE"] == "paper_search_mcp.server"
    assert "PAPER_SEARCH_MCP_SOURCE_PATH" not in spec.env


def test_paper_search_server_passes_research_credentials_without_leaking_them():
    specs = build_server_specs(
        Settings(
            _env_file=None,
            paper_search_mcp_enabled=True,
            paper_search_mcp_openalex_api_key="oa-secret",
            paper_search_mcp_unpaywall_email="research@example.com",
            paper_search_mcp_core_api_key="core-secret",
            paper_search_mcp_semantic_scholar_api_key="s2-secret",
        )
    )
    env = specs["paper-search-mcp"].env
    assert env["PAPER_SEARCH_MCP_OPENALEX_API_KEY"] == "oa-secret"
    assert env["PAPER_SEARCH_MCP_UNPAYWALL_EMAIL"] == "research@example.com"
    assert env["PAPER_SEARCH_MCP_CORE_API_KEY"] == "core-secret"
    assert env["PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY"] == "s2-secret"
    # Credentials travel via the subprocess environment, never as argv —
    # argv is visible to every other process on the host (e.g. `ps aux`).
    for secret in ("oa-secret", "core-secret", "s2-secret"):
        assert secret not in specs["paper-search-mcp"].args

    disabled = build_server_specs(
        Settings(_env_file=None, paper_search_mcp_enabled=False)
    )
    assert "paper-search-mcp" not in disabled


@pytest.mark.asyncio
async def test_mcp_tool_timeout_has_actionable_error():
    class SlowSession:
        async def call_tool(self, name, arguments):
            await asyncio.sleep(1)
            return SimpleNamespace(content=[])

    client = MCPClient(
        servers={},
        tool_timeout_seconds=0.01,
    )
    client._sessions["paper-search-mcp"] = SlowSession()

    with pytest.raises(
        TimeoutError,
        match="paper-search-mcp.search_papers",
    ):
        await client.call_tool(
            "search_papers",
            {"query": "biomass"},
            server="paper-search-mcp",
        )
