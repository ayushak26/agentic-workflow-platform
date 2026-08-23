# Chat Workspace QA Report

**Verdict: PASS**

Certified on **2026-08-23** against the configured local/live environment.

## Live infrastructure and provider certification

- `GET /ready`: passed with MongoDB, Weaviate, MinIO, Redis/checkpointer, local Kimi, scientific skills, and configured MCP startup checks ready.
- MongoDB: owner-scoped saved Knowledge collections, retrieval profiles, generation profiles, and RAG Agents were discovered under owner scope `ayush`.
- Weaviate: live saved-RAG retrieval completed against the active saved collection/index.
- MinIO: round-trip object storage passed; generated PDF and PPTX files had valid signatures, content types, storage records, and presigned URLs.
- Redis/checkpointer: readiness and durable workflow execution/checkpoint coverage passed.
- OpenAI, Anthropic, OpenRouter, Tavily, and local Kimi: direct configured gateway smoke calls passed.
- Scientific skills: startup/readiness and live configured discovery passed.
- MCP: host-side discovery passed for all five configured servers. A separate stale-dependency container invocation was not used as authority because application startup had already loaded and certified the mounted source.
- Saved RAG Agent `rag_01M091FBFENHDRYHX09KW3KH1N`: passed a live authenticated query. Application logs show two OpenRouter embedding calls, reranking of 20 candidates to 6, compression of all 6 retained chunks, local Kimi generation, and final HTTP 200.

## Deterministic and browser coverage

- The workspace exposes exactly 20 unique product experiences.
- Every experience resolves to either a shipped workflow or generated YAML that passes strict compiled preflight.
- Lightweight LLM, direct-file, PDF, and PowerPoint adapters execute through the existing workflow runtime; no second runtime was introduced.
- Direct-file execution uses the real `WorkflowFileLoader` and extraction path with an in-memory object store.
- PDF and PowerPoint execution validates file signatures and object-store content types.
- Multimodal coverage executes Knowledge Retrieval, five configured model stages, image generation, cited chat output, and durable refresh behavior.
- MCP requests fail closed unless both a configured connection ID and tool name are supplied.
- Prior-run artifact follow-ups reuse owner-scoped durable outputs rather than repeating retrieval/source research.
- Conversation-owned runs are durable and directly addressable but excluded from global Workflow Run History and workflow statistics by default.
- Retry and restart preserve workflow, conversation, and message correlation metadata.
- Pipeline and Business View contracts, APIs, routes, and tests remain present in the focused commits.

## Validation results

- Backend workspace planning commit (`734b70f`): **77 passed**; API route checks and diff checks passed.
- Runtime metadata integration (`e00b59b`): **114 passed, 1 skipped** across run history, visibility API, Pipeline, chat workflow, planner, and adapter execution tests; Python compile and diff checks passed.
- Isolated frontend (`499e118`): TypeScript passed; **42 files / 427 tests passed**; ESLint passed; production build passed.
- Focused Playwright coverage: **8 / 8 passed** serially across desktop and small-mobile projects for workspace planning, all 20 experiences, artifact follow-up, Knowledge Retrieval, five-model execution, image generation, and restoration.
- Production build emitted only the existing large-chunk advisory; the focused snapshot intentionally preserves eagerly loaded Pipeline and Business View routes.

## Commit separation

- `734b70f feat(chat): add workflow-neutral workspace planning`
- `e00b59b feat(chat): persist conversation-scoped run metadata`
- `499e118 feat(chat): add workflow-neutral workspace UI`
- This report is committed separately from implementation.

Unrelated Pipeline/Business View removals and generated Playwright artifacts remain outside these commits.