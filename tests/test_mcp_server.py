"""MCP server tests. Imports the server module and calls its handler
functions directly — no subprocess needed. Real subprocess wiring is
exercised by the FastAPI startup test in Phase 9."""
import json
import pytest

import app.mcp.server as srv


async def test_list_tools_returns_three_tools():
    tools = await srv.list_tools()
    names = {t.name for t in tools}
    assert names == {"search_documents", "get_document_chunks", "validate_citation"}


async def test_search_documents_requires_session_id():
    tools = await srv.list_tools()
    search_tool = next(t for t in tools if t.name == "search_documents")
    assert "session_id" in search_tool.inputSchema["required"]


async def test_validate_citation_requires_session_id():
    tools = await srv.list_tools()
    judge_tool = next(t for t in tools if t.name == "validate_citation")
    assert "session_id" in judge_tool.inputSchema["required"]


async def test_call_tool_unknown_raises():
    with pytest.raises(ValueError, match="Unknown tool"):
        await srv.call_tool("nonexistent", {})