# Architecture

The platform is a node-typed AI workflow runtime. YAML workflow definitions are compiled at runtime into LangGraph StateGraphs. Eight node types cover the full agentic surface: RAG, MCP, Human-in-Loop, Router, Transform, plus pre-baked tool nodes for Excel, PowerPoint, and PDF.

The flagship demo workflow is a 9-node proposal-generation workflow that takes an RFP-style document plus client context and produces a styled, cited proposal PDF.

This document is filled out incrementally across Phases 1 through 11.

## Phase plan

1. Repo, Docker Compose, FastAPI skeleton, structlog
2. Document ingestion pipeline (pdfplumber, openpyxl, chunking, MinIO, Weaviate)
3. Hybrid RAG retrieval module (metadata pre-filter, BM25 + vector, LLM rerank)
4. Node framework, NodeRegistry, YAML schema, YAML-to-LangGraph runtime
5. Core nodes: RAG, Transform, Router, Human-in-Loop
6. MCP server plus MCP Agent node
7. Tool nodes: Excel, PowerPoint, PDF
8. Flagship proposal workflow, HTML-to-PDF, stub workflows
9. React frontend: Vite + TS + Tailwind, three-mode UI, React Flow Builder, WebSocket Cockpit
10. Observability (Prometheus + Grafana), LLM-as-a-Judge evaluation
11. Security, session isolation, isolation verifier, Cloud Migration Map UI, interview prep doc

## Node types

| Node               | What it does                                                                   |
|--------------------|--------------------------------------------------------------------------------|
| RAG Agent          | Hybrid retrieval (metadata filter, BM25 + vector, LLM rerank), grounded gen   |
| MCP Agent          | LLM-driven tool selection over an MCP server                                   |
| Human-in-Loop      | LangGraph interrupt(), Cockpit approval/reject/edit                            |
| Router             | Rule-based or LLM-judged branching                                             |
| Transform          | LLM transform: summarize, classify, rewrite, extract                           |
| Excel Tool         | Pre-baked: extract tables from Excel                                           |
| PowerPoint Tool    | Pre-baked: generate slides from sections                                       |
| PDF Tool           | Pre-baked: extract text, render styled proposal PDF                            |

## C4 model

C1 system context, C2 containers, C3 components diagrams: added in Phase 4 when the workflow runtime exists to depict.

Reference: Mastering API Architecture, Introduction (C4 diagrams, ADRs).

## LLM Providers

**Architectural default: Anthropic Claude.** Flagship workflow YAML
(`workflows/proposal_generation.yaml`) and all node-level model
configurations commit to `claude-sonnet-4-5` and `claude-haiku-4-5` per
the Eurskem proposal.

**Build state: OpenAI is the live provider.** Anthropic is a documented
stub in `app/llm/anthropic_gw.py`. Activating Claude live is a single
file's worth of work — restore the `AsyncAnthropic` client (the
implementation is preserved in `docs/anthropic-implementation.md` for
reference) and add `ANTHROPIC_API_KEY` to `.env`.

**Runtime resolution: documented fallback map.** When a workflow YAML
declares an intended model that routes to a stubbed provider, the gateway
applies the fallback in `app/llm/registry.py::_FALLBACK_MODEL`. Every
fallback is logged as `llm.fallback` for observability. This is not just
dev plumbing — it's a degradation pattern. In prod, if the primary
provider is unavailable, the same mechanism routes to a secondary
provider with no workflow changes.

| Intended model         | Routes to             | Live? | Fallback        |
|------------------------|-----------------------|-------|-----------------|
| claude-sonnet-4-5      | AnthropicGateway      | stub  | gpt-5           |
| claude-haiku-4-5       | AnthropicGateway      | stub  | gpt-5-mini      |
| claude-opus-4-7        | AnthropicGateway      | stub  | gpt-5           |
| gpt-5                  | OpenAIGateway         | live  | —               |
| gpt-5-mini             | OpenAIGateway         | live  | —               |
