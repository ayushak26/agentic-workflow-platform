# Knowledge Studio and Advanced RAG Engineering Report

Date: 2026-08-13

This report closes the Knowledge Studio implementation pass against this
specific checkout. It should be read with the [repository audit](KNOWLEDGE_STUDIO_AUDIT.md),
which describes the pre-existing state this work builds on, and with
`SECURITY.md`/`CODEBASE_HANDBOOK.md` where those overlap.

An earlier version of this report (produced outside this checkout, before
any of the code below existed) described a completed 700-test regression
run. That report did not match this repository — none of the modules it
claimed as "added" were present. This version reports only what was
actually built and verified in this checkout, with real numbers from real
test runs.

## 1. What existed before this pass

The legacy spine was already correct and untouched by this work: `extractor.py
→ chunker.py → embedder.py → pipeline.py` for ingestion, `query_understanding.py
→ hybrid_search.py → reranker.py → compressor.py` for retrieval, one shared
Weaviate `DocumentChunk` collection, Mongo `manifests`/`collections`, and the
ordinary registered `RAGAgent` workflow node. None of `app/knowledge/`,
`app/rag/`, the retrieval/ingestion stage layers, or the three new API
routers existed anywhere in this checkout before this pass.

## 2. Architecture implemented

The control plane now resolves resources independently of the legacy
pipeline, per the target model:

| Product question | Resource |
|---|---|
| What knowledge exists? | Logical Collection (`app/knowledge/models.py::CollectionResource`) |
| How was it prepared? | Versioned parser/chunking/embedding profiles + Ingestion Job |
| Which searchable representation? | Index Version |
| How should it be searched? | Retrieval Profile, optionally chosen by a Routing Profile |
| How should it answer? | RAG Agent + Generation Profile |
| Why and when is it used? | The existing Workflow runtime, unchanged |

The runtime remains exactly `WorkflowSpec → zero-token preflight →
NodeRegistry → LangGraph → _make_runtime_fn → node execution → run
history/events/audit/cost`. Knowledge Studio is services inside that
platform; no second FastAPI app, workflow engine, or queue was introduced.

## 3. Files added

21 new files, none of which existed in this checkout before this pass:

- `app/knowledge/{__init__,ids,models,repository,service}.py` — opaque IDs,
  typed resources, owner-scoped Mongo repository, lifecycle service.
- `app/ingestion/{contracts,strategies,jobs,indexes,coordinator}.py` —
  stage protocols, parser/chunker/enricher implementations, the durable job
  runner, the Weaviate index writer, and the background coordinator that
  bridges uploaded sources to local paths and recovers interrupted jobs.
- `app/retrieval/{service,strategies,filters,fusion,context,query_transform,presets,contracts}.py`
  — the canonical staged `RetrievalService`, dense/sparse/hybrid execution,
  typed metadata filters + the non-removable security clause, RRF, context
  expansion, query transforms, and Playground presets.
- `app/rag/{__init__,models,service}.py` — saved RAG Agent resolution
  (route → collection → retrieval profile → index → generation) and
  grounded generation.
- `app/api/{knowledge,retrieval,rag_agents}.py` — the three product routers.
- `app/nodes/knowledge_retrieval.py` — the one retrieval-only NodeType.
- `scripts/backfill_knowledge_resources.py` — idempotent legacy backfill.
- `samples/knowledge_demo/` — four fictional Dura 25 pump documents (product
  manual, chemical compatibility guide, troubleshooting guide, selection
  guide), used by the Playground's default demo query.
- `ui/src/api/knowledge.ts`, `ui/src/modes/knowledge/` (`KnowledgeRoot`,
  `CollectionsPage`, `IngestionPage`, `DocumentsIndexesPage`,
  `PlaygroundPage`, `ProfilesAgentsPage`, `TracesPage`, `shared.tsx`,
  `config.ts`) — the full Knowledge Studio UI surface.
- `tests/test_knowledge_resources.py`, `test_ingestion_strategies.py`,
  `test_retrieval_pipeline_stages.py`, `test_knowledge_security.py`,
  `test_knowledge_legacy_backfill.py` — 72 new backend tests.

## 4. Files changed

18 existing files were extended — every change is additive (new optional
fields, new methods, new dict entries); no existing call site's behavior
changed:

| File | Change |
|---|---|
| `app/main.py` | Constructs `KnowledgeRepository`/`KnowledgeService`/`RetrievalService`/`RAGService`/`IngestionCoordinator` from the same Mongo/Weaviate/embedder/LLM clients already wired; registers the 3 new routers; runs ingestion recovery at startup. |
| `app/retrieval/models.py` | Added `MetadataFilterGroup`/`MetadataFilterPredicate`; extended `RetrievalFilters`/`RetrievalQuery`/`RetrievedChunk`/`RetrievalResult` with the fields the staged pipeline and traces need. Existing legacy construction call sites are untouched — every new field has a default. |
| `app/ingestion/chunker.py` | `Chunk` gained `retrieval_content`/`context_content`/`title`/`section`/`parent_chunk_id`/`chunk_role` and an `embedding_content` property (fixes a real bug in the originally pasted code: `jobs.py` read `chunk.embedding_content`, which nothing defined). `ChunkConfig` gained `parent_tokens`/`sentence_window`. |
| `app/retrieval/weaviate_client.py` | Added the Knowledge Studio provenance/search fields to `SCHEMA_PROPERTIES`; added `ensure_collection_schema_on`/`upsert_objects_on` module functions so ingestion can target a per-index physical collection using the same raw client `services["weaviate_client"]` already holds — no second Weaviate connection. |
| `app/db/mongo.py` | Added 9 collection-name constants (`knowledge_collections`, `knowledge_documents`, `knowledge_source_versions`, `knowledge_profiles`, `knowledge_indexes`, `knowledge_index_documents`, `ingestion_jobs`, `rag_agents`, `retrieval_traces`). Legacy `manifests`/`collections` untouched. |
| `app/db/migrations.py` | Added `0002_knowledge_resources_v2`: backfills missing lifecycle fields onto legacy `collections` documents in place. Never touches `owner_scope_id` — globally-scoped legacy records stay globally scoped. |
| `app/security/rbac.py` | Added `knowledge:read`, `knowledge:write`, `rag:query`, `rag:write` to Admin/Consultant; Viewer gets read + query only. |
| `app/observability/metrics.py` | Added 6 bounded-label counters/histograms (`strategy`/`status`/`stage` only — never query, collection_id, document_id, or filename). |
| `app/storage/minio_client.py` | Added `knowledge_key_for_path`: content-addressed, isolated by a digest of the owner scope rather than a raw name. |
| `app/config.py` | Added `retrieval_trace_retention_days` (default 30). |
| `app/runtime/preflight.py` | Added the one check that is genuinely zero-token and self-contained: `RAG_RUNTIME_FILTER_UNSAFE` rejects a `runtime_filters` key that names a reserved security/provenance field, for both `RAGAgent` and `KnowledgeRetrieval`. See §11 for what was *not* added here and why. |
| `app/api/inspect.py` | `/api/inspect/retrieve` now delegates to `services["retrieval_service"]` when it's wired, instead of calling the retrieval pipeline a second, independent way; falls back to the old compatibility adapter only when Mongo/Weaviate were never wired. |
| `app/nodes/rag.py` | Replaced with the dual-mode version: `rag_agent_id` (preferred, calls `rag_service`) or legacy inline knobs (unchanged behavior, calls `retriever`). A `model_validator` rejects mixing the two. |
| `tests/fake_mongo.py` | Additive: `find_one` gained an optional `sort` kwarg, `_Cursor.sort` now actually sorts (was a no-op), and `replace_one` was added. Verified against all 8 existing consumer test files — 64/64 still pass. |
| `tests/test_node_preflight_coverage.py` | Added `KnowledgeRetrieval` to `ACKNOWLEDGED_NODE_TYPES` with its coverage rationale, per that test's own review requirement. |
| `.gitignore` | `samples/` → `samples/*` + `!samples/knowledge_demo/`, so the fictional demo corpus is trackable while other local sample data stays ignored. |
| `ui/src/App.tsx`, `Sidebar.tsx`, `Topbar.tsx` | Added the `"knowledge"` mode alongside `studio`/`eval`/`operator`. |

## 5. Mongo, Weaviate, migrations

Covered in §4 above (`app/db/mongo.py`, `migrations.py`,
`weaviate_client.py`). Weaviate's `DocumentChunk` collection remains the
default; a differently-fingerprinted embedding profile gets its own
`DocumentChunk_<fingerprint>` collection via `ensure_collection_schema_on`,
never sharing an HNSW index across incompatible vector dimensions.

## 6. Ingestion and retrieval strategies implemented

Parsers: standard, layout-aware, structure-aware, OCR-fallback (threshold-
gated). Chunkers: fixed-token, recursive, structure-aware, parent/child,
contextual, sentence-window, and a lightweight lexical-overlap semantic
splitter. Retrieval: dense, sparse/BM25, native Weaviate hybrid
(relative-score), independent dense+sparse RRF, deduplication, reranking,
compression, typed metadata filters (validated against the collection
schema, reserved fields always rejected), and parent/sentence-window/
contextual expansion. Query transforms (rewrite, multi-query,
decomposition, HyDE, self-query) all degrade to a no-op if no LLM is
available — verified directly in `test_retrieval_pipeline_stages.py`.

## 7. Legacy compatibility preserved

- The existing `RAGAgent` node's legacy inline mode (`top_k_candidates`,
  `alpha`, `rerank`, ...) still calls `services["retriever"]` exactly as
  before; nothing about that path changed.
- `app/api/inspect.py`'s `/chunks` view is untouched (a raw Weaviate listing,
  not a retrieval reimplementation); its `/retrieve` view now shares
  `RetrievalService` instead of a parallel implementation.
- The backfill script (§9) never re-mints a `collection_id`, and legacy
  Weaviate objects stay queryable through a "Legacy Index v1" whose
  `physical_index_id` is intentionally `None`.

## 8. Security

`RetrievalService._retrieve_impl` raises `RetrievalAuthorizationError` before
any I/O if `filters.session_id != owner_scope_id` — verified directly, not
just through integration. `RESERVED_METADATA_FIELDS` (`workspace_id`,
`session_id`, `owner_scope_id`, `collection_id`, `index_id`, `document_id`,
`source_id`, `source_version_id`) is checked in three independent places:
`validate_metadata_filters` (runtime), the zero-token preflight check on
`runtime_filters` (compile time), and `self_query`'s generated-filter path
(which drops, rather than raises on, an invalid field — a malformed
self-query must degrade, not fail the whole retrieval). RAG citations carry
`evidence_status: "retrieved_not_verified"` and never touch the
`VerifiedClaim` lifecycle.

## 9. Migrations and backfill

```bash
uv run python scripts/backfill_knowledge_resources.py            # dry run
uv run python scripts/backfill_knowledge_resources.py --apply    # apply
```

Owner scope for legacy data is derived from
`manifest["metadata"]["session_id"]` — the same field the legacy
`app/ingestion/pipeline.py` already stamped on every manifest (defaulting to
`"default"`, its own convention, not one invented here). The script is
idempotent (a second `--apply` run reports `collections_existing`, creates
nothing) and never assigns a fabricated owner. Verified end-to-end against
an in-memory Mongo fake in `tests/test_knowledge_legacy_backfill.py`,
including the idempotency property.

## 10. UI

Knowledge Studio is now the fourth top-level mode (`Studio`, `Knowledge
Studio`, `Evaluation Lab`, `Operator Console`), with Collections, Ingestion,
Documents & Indexes, Retrieval Playground, Profiles & RAG Agents, and Traces
tabs. Copyable opaque IDs, ingestion presets, per-document outcomes, index
activation, stage-by-stage retrieval detail, three-way strategy comparison,
saved Retrieval Profiles, RAG Agent creation and test queries, and raw trace
inspection are all wired to the real API — not mocked.

## 11. Known limitations and explicitly deferred work

- **Existence-checking preflight rules were not added.** The handover asked
  for zero-token checks like `RAG_AGENT_NOT_FOUND`, `COLLECTION_NOT_FOUND`,
  `INDEX_NOT_READY`, `EMBEDDING_PROFILE_INCOMPATIBLE`. Those require a live,
  owner-scoped Mongo read from inside `app/runtime/preflight.py` (2,150+
  lines, load-bearing for every existing node type), and the compile-time
  entry points that check YAML shape have no request-scoped repository to
  query from. Only the one check requiring zero I/O
  (`RAG_RUNTIME_FILTER_UNSAFE`) was added. Wiring the rest belongs in the
  async, per-run probe stage (`_probe_services`/`preflight_workflow_for_run`)
  and deserves its own focused pass rather than a same-session edit to this
  file's riskiest section.
- **Builder/Cockpit RAG Agent selector UI was not built.** `RAGAgent`'s
  `rag_agent_id` field is already marked `x-preferred` in its config schema
  and works end-to-end from YAML; a dedicated Builder picker and Cockpit
  trace-link button are follow-up UI work, not backend gaps.
- **Evaluation Lab was not extended** with retrieval-specific datasets/
  metrics (Recall@K, MRR, NDCG); `app/evaluation/retrieval_metrics.py` was
  never created.
- Semantic chunking remains a lightweight lexical-overlap heuristic, not an
  embedding-distance splitter.
- Parent/sentence-window/contextual expansion depends on content ingested
  with those relationships; it cannot retrofit relationships onto untouched
  legacy chunks.
- The pre-existing large-chunk Vite bundle-size advisory is unchanged by
  this work.

## 12. Test results (this checkout, this session)

| Verification | Result |
|---|---|
| `python -m compileall app/` | Clean |
| Backend regression (`pytest tests/`) | **1122 passed, 3 skipped, 7 failed** |
| New Knowledge Studio backend tests | **72 passed** (5 new files) |
| `git stash` isolation of the 7 failures | Reproduce identically at HEAD with none of this pass's changes applied — confirmed pre-existing and unrelated (candidate-dedup TypeError, a missing `horizon_proposal_hitl_pdf.yaml` fixture, a scientific-skills prompt-bundle assertion) |
| Frontend unit tests (`npm test -- --run`) | **142 passed** across 13 files |
| `npx tsc -b` | Clean |
| `npx eslint` (Knowledge Studio files + touched nav files) | Clean (18 pre-existing errors elsewhere, in `ui/src/modes/studio/builder/`, untouched by this work) |
| `npm run build` | Passed; only the pre-existing large-chunk advisory |

The 7 backend failures were verified pre-existing by reverting every file
this pass touched (`git stash`) and re-running them against unmodified HEAD:
identical failures, identical count. None are Knowledge Studio code.

## 13. Complete walkthrough

1. **Demo files**: `samples/knowledge_demo/` — four Dura 25 pump documents.
2. **Collection**: create one in the Collections tab, copy `col_…`.
3. **Ingestion**: pick a preset, choose parser/chunking, upload the demo
   files, watch the stage-by-stage job progress.
4. **Index**: activate the resulting `idx_…` from Documents & Indexes.
5. **Playground**: ask "How should the Dura 25 be used with sodium
   hypochlorite?" — the demo corpus actually contains a grounded answer for
   this. Compare Dense vs Hybrid vs Hybrid+Reranker; inspect every stage.
6. **Retrieval Profile**: save a tested configuration as `retprof_…`.
7. **RAG Agent**: bind collection + retrieval + generation profile; test the
   grounded answer and citations; copy `rag_…`.
8. **Workflow**: `type: RAGAgent`, `config: {rag_agent_id: rag_…, query: "..."}`
   — runs through the unchanged runtime, with resolved index/profile
   versions recorded on the output and in the retrieval trace.
