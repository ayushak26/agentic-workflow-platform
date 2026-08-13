# Knowledge Studio Repository Audit

This is the Phase 0 current-state report used for the Knowledge Studio redesign. It is based on the repository implementation, its tests, and the supplied engineering handbooks; historical diagrams were treated as context rather than source of truth.

## 1. Current-state architecture

The existing platform already had the correct runtime and storage spine: YAML is validated into `WorkflowSpec`, zero-token preflight resolves the `NodeRegistry`, the compiler builds LangGraph, `_make_runtime_fn` applies the common execution boundary, and run history/events/audit/cost are recorded. RAG was an ordinary registered node and therefore already inherited those guarantees. Mongo stored ingestion manifests and collection configuration, MinIO stored immutable uploads, and one Weaviate `DocumentChunk` collection supplied vectors and BM25.

## 2. Current resource/data model

Before this redesign, `CollectionConfig` and ingestion manifests were the durable RAG control-plane records. Documents were principally identified by source path/MinIO key. Chunks carried useful `session_id`, `collection_id`, source, unit/chunk indexes and format metadata, but there was no separately lifecycle-managed document, source-version, profile, index-version, RAG-agent or trace resource.

## 3. Current ingestion flow

`extractor.py → chunker.py → embedder.py → pipeline.py` performed structure-aware extraction, recursive token-aware chunking, embedding, MinIO upload, Weaviate writes and idempotent manifest updates. Its strongest properties were preserved: deterministic content hashes, source provenance, async wrappers around synchronous SDKs, and the recursive hierarchy of section → paragraph → sentence → token cut.

## 4. Current retrieval flow

`query_understanding.py → hybrid_search.py → reranker.py → compressor.py` implemented optional rewrite, Weaviate relative-score hybrid search, reranking and contextual compression. Mandatory session and collection constraints were applied before candidates escaped the datastore. `inspect.py` had a partially independent compatibility adapter, creating drift risk.

## 5. Current RAGAgent contract

`RAGAgent` accepted inline model, candidate/final counts, alpha, rewrite/rerank/compress flags, filters and generation prompt. It returned an answer plus chunks/citations, but workflows had to repeat retrieval internals and historical runs did not resolve a stable saved RAG resource graph.

## 6. Current UI capabilities

Corpus Inspector showed chunks and retrieval preview; Evaluation Lab evaluated workflows; Builder exposed the generic node config; Cockpit showed common node output/run telemetry. There was no dedicated collection/ingestion/profile/index/RAG-agent lifecycle surface, no stage trace, and no saved experiment flow.

## 7. Current security boundaries

Authentication/RBAC protected APIs. Retrieval pinned `session_id` and `collection_id` inside the Weaviate filter. MinIO keys and manifests retained source identity. The important boundary was correct, but the legacy inspector and free-form filters needed consolidation and explicit tests. RAG citations remained separate from the fail-closed `VerifiedClaim` evidence lifecycle.

## 8. Gaps against the target

The material gaps were first-class stable resources, profile/index versioning, stage interfaces, durable ingestion jobs, canonical retrieval execution/traces, typed metadata filtering, RAG-agent persistence, exact run-time resolution, Knowledge Studio UI, Builder selection, Cockpit trace links, and retrieval-specific evaluation metrics.

## 9. Migration risks

| Risk | Mitigation |
|---|---|
| Existing collection IDs stop resolving | Preserve aliases and deterministic compatibility mappings. |
| Existing Weaviate objects lack `index_id` | Backfill a logical Legacy Index v1 whose physical index filter is intentionally absent. |
| Different embedding dimensions share a physical vector index | Map indexes to a narrow Weaviate adapter collection keyed by embedding fingerprint. |
| Legacy workflow YAML breaks | Keep inline `RAGAgent` parsing/execution; new Builder nodes prefer `rag_agent_id`. |
| User metadata widens access | Compile immutable scope clauses separately and AND typed user clauses. |
| Inspector/eval diverge from production | Route RAG, workflows, inspector, Playground and evaluation through `RetrievalService`. |
| Active index changes destroy reproducibility | Persist resolved IDs and exact profile versions in node output and retrieval traces. |
| Citations are mistaken for verified evidence | Mark RAG citations `retrieved_not_verified`; preserve verification nodes and policies. |

## 10. File-by-file implementation plan

| Area | Files | Change |
|---|---|---|
| Resource model | `app/knowledge/*`, `app/db/migrations.py`, `app/db/mongo.py` | Scoped resources, stable IDs, repositories, services and legacy backfill. |
| Ingestion | `app/ingestion/contracts.py`, `strategies.py`, `jobs.py`, `coordinator.py`, existing extractor/chunker/embedder | Versioned stage strategies and recoverable jobs around the working pipeline. |
| Retrieval | `app/retrieval/service.py`, `filters.py`, `strategies.py`, `fusion.py`, `context.py`, existing Weaviate/rerank/compress modules | One canonical staged executor and trace. |
| RAG/workflow | `app/rag/*`, `app/nodes/rag.py`, `knowledge_retrieval.py`, `app/runtime/preflight.py` | Saved agents, compatibility mode, typed outputs and zero-token resource checks. |
| APIs | `app/api/knowledge.py`, `retrieval.py`, `rag_agents.py`, `inspect.py`, `eval.py` | Lifecycle, testing, comparison, traces and evaluation. |
| UI | `ui/src/modes/knowledge/*`, Builder/Cockpit/Eval/Operator modules | One Knowledge Studio, saved-agent authoring and trace visibility. |
| Operations | `app/observability/metrics.py`, `.env.example`, `scripts/backfill_knowledge_resources.py` | Bounded metrics, trace retention and idempotent migration. |
