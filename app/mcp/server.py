"""MCP server: exposes hybrid retrieval as protocol-level tools.

Runs as a stdio subprocess. The FastAPI app launches this at startup via
app/mcp/client.py and keeps a session open for the lifetime of the process.

Tools:
  - search_documents: hybrid retrieval over the knowledge base, slim results
  - get_document_chunks: fetch full text for one or more chunk_ids
  - validate_citation: LLM-as-judge scoring whether a chunk supports a claim
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from app.ingestion.embedder import get_embedder
from app.llm import get_llm_gateway
from app.observability.logging import get_logger
from app.retrieval.models import RetrievalFilters, RetrievalQuery
from app.retrieval.retriever import retrieve
from app.retrieval.weaviate_client import get_weaviate_client

log = get_logger(__name__)
server: Server = Server("agentic-workflow-rag")

# Clients are lazy — the subprocess shouldn't pay startup cost until the
# first tool call. Caching here avoids reconnecting on every invocation.
_cached: dict[str, Any] = {}


def _services() -> dict[str, Any]:
    if not _cached:
        _cached["weaviate"] = get_weaviate_client().connect()
        _cached["embedder"] = get_embedder()
        _cached["llm"] = get_llm_gateway()
    return _cached


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_documents",
            description=(
                "Hybrid BM25+vector search over the knowledge base with "
                "metadata filters. Returns slim chunk summaries — use "
                "get_document_chunks to fetch full text for the ones you want."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "session_id": {"type": "string",
                        "description": "Workflow session id — passed by the agent runtime."},
                    "industry": {"type": "string"},
                    "collection_ids": {"type": "array", "items": {"type": "string"}},
                    "top_k": {"type": "integer", "default": 8},
                },
                "required": ["query", "session_id"],
            },
        ),
        types.Tool(
            name="get_document_chunks",
            description="Fetch full text for one or more chunk_ids from a prior search.",
            inputSchema={
                "type": "object",
                "properties": {
                    "chunk_ids": {"type": "array", "items": {"type": "string"}},
                    "session_id": {"type": "string"},
                },
                "required": ["chunk_ids", "session_id"],
            },
        ),
        types.Tool(
            name="validate_citation",
            description=(
                "LLM-as-judge: returns a score 0.0-1.0 indicating whether the "
                "given chunk supports the claim. Use this to verify citations "
                "before including them in a final answer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string"},
                    "claim": {"type": "string"},
                    "session_id": {"type": "string"},
                },
                "required": ["chunk_id", "claim", "session_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    log.info("mcp.tool_call", tool=name, session_id=arguments.get("session_id"))

    # Validate the tool name BEFORE initializing services. This means the
    # unknown-tool path doesn't pay Weaviate's connection cost, and tests
    # for this path run without Docker.
    known_tools = {"search_documents", "get_document_chunks", "validate_citation"}
    if name not in known_tools:
        raise ValueError(f"Unknown tool: {name}")

    svc = _services()

    if name == "search_documents":
        filters = RetrievalFilters(
            session_id=arguments["session_id"],
            industry=arguments.get("industry"),
            collection_ids=arguments.get("collection_ids"),
        )
        q = RetrievalQuery(
            query=arguments["query"],
            filters=filters,
            top_n_final=arguments.get("top_k", 8),
        )
        result = await retrieve(
            q, weaviate_client=svc["weaviate"], llm=svc["llm"], embedder=svc["embedder"],
        )
        slim = [{
            "chunk_id": c.chunk_id,
            "source_doc": c.doc_title,
            "doc_type": c.doc_type,
            "snippet": (c.text[:240] + "...") if len(c.text) > 240 else c.text,
            "score": c.hybrid_score,
        } for c in result.chunks]
        return [types.TextContent(type="text", text=json.dumps({"chunks": slim}))]

    if name == "get_document_chunks":
        # Direct Weaviate read by chunk_id, session-scoped for isolation
        from weaviate.classes.query import Filter
        collection = svc["weaviate"].collections.get("DocumentChunk")
        results = []
        for cid in arguments["chunk_ids"]:
            response = collection.query.fetch_objects(
                filters=(
                    Filter.by_property("chunk_id").equal(cid)
                    & Filter.by_property("session_id").equal(arguments["session_id"])
                ),
                limit=1,
            )
            if response.objects:
                obj = response.objects[0]
                results.append({
                    "chunk_id": cid,
                    "source_doc": obj.properties.get("source_path", ""),
                    "text": obj.properties["text"],
                })
        return [types.TextContent(type="text", text=json.dumps({"chunks": results}))]

    if name == "validate_citation":
        # Fetch the chunk, then ask the LLM to score support
        from weaviate.classes.query import Filter
        collection = svc["weaviate"].collections.get("DocumentChunk")
        response = collection.query.fetch_objects(
            filters=(
                Filter.by_property("chunk_id").equal(arguments["chunk_id"])
                & Filter.by_property("session_id").equal(arguments["session_id"])
            ),
            limit=1,
        )
        if not response.objects:
            return [types.TextContent(type="text", text=json.dumps({
                "score": 0.0, "reason": "chunk not found or not in this session"
            }))]
        chunk_text = response.objects[0].properties["text"]
        judge_prompt = (
            "Score from 0.0 to 1.0 how strongly the SOURCE supports the CLAIM. "
            "1.0 = directly stated. 0.5 = implied. 0.0 = unsupported or contradicted. "
            "Respond with JSON: {\"score\": float, \"reason\": str}\n\n"
            f"SOURCE: {chunk_text}\n\nCLAIM: {arguments['claim']}"
        )
        judge_raw = await svc["llm"].chat(
            model="claude-sonnet-4-5",
            messages=[{"role": "user", "content": judge_prompt}],
            temperature=0.0,
        )
        # Best-effort JSON parse; the LLM was instructed to return JSON.
        try:
            parsed = json.loads(judge_raw)
        except json.JSONDecodeError:
            parsed = {"score": 0.0, "reason": "judge output was not JSON"}
        return [types.TextContent(type="text", text=json.dumps(parsed))]

    raise ValueError(f"Unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())