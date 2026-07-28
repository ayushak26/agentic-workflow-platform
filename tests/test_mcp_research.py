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
    assert spec.args == ["-m", "paper_search_mcp.server"]
    assert "python" in spec.command


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
