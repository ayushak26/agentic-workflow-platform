# Interview Prep

Phase-by-phase interview answers. Each phase contributes a section.

Filled out fully in Phase 11. The structure: per question, the concise answer (interview-deliverable, under 60 seconds), then the deeper context for follow-up questions, then the resume bullet it maps to.

## Main story (one paragraph)

I built an agentic workflow platform: a node-typed runtime where users compose AI workflows from RAG agents, MCP agents, human-in-loop agents, routers, transforms, and tool nodes for Excel, PowerPoint, and PDF. A 9-agent proposal generator runs as the flagship demo workflow on top. Underneath, it is FastAPI orchestrating LangGraph workflows compiled at runtime from YAML definitions, hybrid Weaviate RAG with re-ranking, an MCP server, structured observability with Prometheus and Grafana, and an LLM-as-a-Judge evaluation harness that can evaluate any workflow.
