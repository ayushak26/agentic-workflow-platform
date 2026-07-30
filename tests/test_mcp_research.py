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


def test_openaire_legacy_fallback_uses_supported_keywords_param():
    """paper-search-mcp 0.1.4's OpenAIRE legacy fallback (`/search/publications`)
    sends the query under a `query` param that endpoint has never supported —
    it 400s even for a plain query. Our launcher patches `_get` to rename it
    to `keywords`, the parameter OpenAIRE's own error message says is
    supported."""
    from app.mcp.paper_search_server import patch_openaire_query_param
    from paper_search_mcp.academic_platforms.openaire import OpenAiresearcher

    patch_openaire_query_param()
    seen = {}

    class FakeResponse:
        status_code = 200

    searcher = OpenAiresearcher()

    def fake_session_get(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs.get("params")
        return FakeResponse()

    searcher.session.get = fake_session_get
    searcher._get(f"{searcher.BASE_URL}/search/publications", params={"query": "test", "size": 3})

    assert "keywords" in seen["params"]
    assert seen["params"]["keywords"] == "test"
    assert "query" not in seen["params"]


def test_openaire_query_patch_leaves_other_endpoints_untouched():
    from app.mcp.paper_search_server import patch_openaire_query_param
    from paper_search_mcp.academic_platforms.openaire import OpenAiresearcher

    patch_openaire_query_param()
    seen = {}

    class FakeResponse:
        status_code = 200

    searcher = OpenAiresearcher()
    searcher.session.get = lambda url, **kwargs: (seen.update(params=kwargs.get("params")), FakeResponse())[1]
    searcher._get(f"{searcher.BASE_URL}/search/researchProducts", params={"keywords": "test"})

    assert seen["params"] == {"keywords": "test"}


def test_semantic_scholar_empty_results_do_not_raise():
    """The Semantic Scholar Graph API omits the `data` key entirely (not an
    empty list) when a query's total is 0. The vendored connector does
    `response.json()["data"]` unconditionally, which raises a KeyError for a
    perfectly normal zero-result query. Our launcher patches
    `request_api` so `.json()` always has a `data` key."""
    from app.mcp.paper_search_server import patch_semantic_scholar_empty_results
    from paper_search_mcp.academic_platforms.semantic import SemanticSearcher

    patch_semantic_scholar_empty_results(min_interval_seconds=0)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"total": 0, "offset": 0}  # real S2 shape: no "data" key

        def raise_for_status(self):
            pass

    searcher = SemanticSearcher()
    searcher.session.get = lambda *a, **k: FakeResponse()

    papers = searcher.search("a query with zero matches", max_results=5)
    assert papers == []


def test_semantic_scholar_requests_are_paced_to_the_rate_limit():
    """Our keyed Semantic Scholar tier is capped at 1 request/second,
    cumulative across all endpoints. Bursting past it 429s every call after
    the first, so requests must be paced proactively rather than only
    reacting to a 429 after it happens."""
    import time as time_module

    from app.mcp.paper_search_server import patch_semantic_scholar_empty_results
    from paper_search_mcp.academic_platforms.semantic import SemanticSearcher

    patch_semantic_scholar_empty_results(min_interval_seconds=0.2)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"total": 0, "offset": 0}

        def raise_for_status(self):
            pass

    searcher = SemanticSearcher()
    searcher.session.get = lambda *a, **k: FakeResponse()

    started = time_module.monotonic()
    searcher.search("first call", max_results=1)
    searcher.search("second call", max_results=1)
    elapsed = time_module.monotonic() - started

    assert elapsed >= 0.2


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
