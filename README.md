# Agentic Workflow Platform

A learning project: a node-typed AI workflow runtime where users compose workflows from RAG agents, MCP agents, human-in-loop agents, routers, transforms, and tool nodes for Excel, PowerPoint, and PDF.

The flagship demo workflow is a 9-agent proposal generator that takes an RFP and produces a styled, cited PDF. The architecture follows the patterns covered in *AI Engineering* (Chip Huyen), *Practical MLOps* (Noah Gift), and *Mastering API Architecture*.

Everything runs locally on Docker Compose. Production deployment paths to AWS, Azure, and GCP are documented but not executed.

## Status

Phase 1 of 11 complete. See `docs/architecture.md` for the phase plan and `docs/adrs/` for architecture decision records.

## Stack

- **Backend:** FastAPI (async), LangGraph, LangChain (templates only)
- **Vector DB:** Weaviate
- **Object storage:** MinIO (S3-protocol compatible)
- **Metadata DB:** MongoDB
- **Cache:** Redis
- **LLMs:** Anthropic Claude (default), pluggable provider gateway
- **Observability:** structlog, Prometheus, Grafana
- **MCP:** Python SDK, stdio transport
- **Frontend (Phase 9+):** React 18 + TypeScript + Vite + Tailwind + React Flow

## Quickstart

    cp .env.example .env
    make up
    curl localhost:8000/health
    curl localhost:8000/ready
    make logs

## Daily commands

| Command       | What it does                              |
|---------------|-------------------------------------------|
| `make up`     | Start all services                        |
| `make down`   | Stop services (keeps data)                |
| `make logs`   | Tail app logs                             |
| `make ps`     | Show running containers                   |
| `make test`   | Run pytest inside the app container       |
| `make fmt`    | Format + lint with ruff                   |
| `make obs-up` | Start Prometheus + Grafana too            |
| `make clean`  | Stop services and wipe data (destructive) |

## Project layout

    app/         FastAPI app, node framework, retrieval, observability
    workflows/   YAML workflow definitions
    ui/          React frontend (Phase 9+)
    docs/        Architecture, cloud migration, security, eval, citations
    tests/       Pytest test suite
