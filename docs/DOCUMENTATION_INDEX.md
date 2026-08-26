# Eurskem AI — Engineering Documentation Portfolio

Generated from commit `8bd16ca4a2a3` on 2026-08-24.

```bash
uv run python scripts/build_documentation_pdf.py
```

## Canonical PDF Portfolio (`docs/pdf/`)

| # | PDF | Scope | Pages |
|---:|---|---|---:|
| 00 | `00_CODEBASE_START_HERE.pdf` | **Codebase Start Here.** Orientation, reading path, setup, and the system mental model. | 3 |
| 01 | `01_SYSTEM_ARCHITECTURE.pdf` | **System Architecture.** Boundaries, services, topology, and principal request flows. | 3 |
| 02 | `02_TECH_STACK_AND_DECISIONS.pdf` | **Tech Stack and Decisions.** Technology inventory and evidence-backed design choices. | 4 |
| 03 | `03_BACKEND_CODE_REFERENCE.pdf` | **Backend Code Reference.** Package, module, class, and function reference for app/. | 174 |
| 04 | `04_FRONTEND_CODE_REFERENCE.pdf` | **Frontend Code Reference.** React modes, components, hooks, API clients, and types. | 235 |
| 05 | `05_FRONTEND_BACKEND_MAPPING.pdf` | **Frontend–Backend Mapping.** UI surfaces mapped to API routes, stores, and runtime behavior. | 13 |
| 06 | `06_WORKFLOW_ENGINE_AND_NODE_TYPES.pdf` | **Workflow Engine and Node Types.** Workflow contracts, compiler, preflight, registry, and node catalog. | 25 |
| 07 | `07_RUNTIME_EXECUTION_AND_STATE.pdf` | **Runtime Execution and State.** Execution lifecycle, state transitions, retries, HITL, and resume. | 3 |
| 08 | `08_DATA_STORAGE_AND_STATE_MANAGEMENT.pdf` | **Data Storage and State Management.** MongoDB, Redis, MinIO, Weaviate, checkpoints, and browser state. | 3 |
| 09 | `09_AI_LLM_MODEL_ROUTING.pdf` | **AI, LLM, and Model Routing.** Providers, model routing, fallback, policy, and cost accounting. | 3 |
| 10 | `10_RAG_KNOWLEDGE_AND_RETRIEVAL.pdf` | **RAG, Knowledge, and Retrieval.** Ingestion, indexing, secure filtering, hybrid search, and grounded generation. | 21 |
| 11 | `11_MCP_TOOLS_AND_INTEGRATIONS.pdf` | **MCP, Tools, and Integrations.** MCP discovery, business systems, email, Drive, and external actions. | 5 |
| 12 | `12_API_REFERENCE_AND_REQUEST_FLOWS.pdf` | **API Reference and Request Flows.** HTTP route catalog, authorization, payloads, and sequences. | 12 |
| 13 | `13_SECURITY_AUTH_AND_DATA_PROTECTION.pdf` | **Security, Auth, and Data Protection.** Identity, RBAC, ownership, isolation, middleware, and sensitive data. | 13 |
| 14 | `14_EVENTS_ASYNC_AND_DISTRIBUTED_COORDINATION.pdf` | **Events, Async, and Distributed Coordination.** SSE, Redis streams, background work, leases, and multi-worker behavior. | 2 |
| 15 | `15_TESTING_DEBUGGING_AND_OBSERVABILITY.pdf` | **Testing, Debugging, and Observability.** Test layers, diagnostics, logging, metrics, traces, and QA evidence. | 4 |
| 16 | `16_DEPLOYMENT_CONFIGURATION_AND_OPERATIONS.pdf` | **Deployment, Configuration, and Operations.** Configuration, containers, CI/CD, deployment, backup, and recovery. | 6 |
| 17 | `17_FILE_BY_FILE_CODE_REFERENCE.pdf` | **File-by-File Code Reference.** Purpose and major symbols for every first-party source file. | 68 |
| 18 | `18_CODE_LOGIC_REFERENCE.pdf` | **Code Logic Reference.** Important functions, methods, branching logic, invariants, and failure behavior. | 150 |
| 19 | `19_DOCSTRINGS_AND_CODE_DOCUMENTATION.pdf` | **Docstrings and Code Documentation.** Documentation coverage, public symbols, conventions, and gaps. | 228 |
| 20 | `20_ARCHITECTURE_TRADEOFFS_TECH_DEBT.pdf` | **Architecture Tradeoffs and Tech Debt.** Confirmed compromises, coupling, risks, removals, and remediation guidance. | 12 |
| 21 | `21_CHANGE_IMPACT_GUIDE.pdf` | **Change Impact Guide.** Change scenarios, affected layers, required tests, and rollout checks. | 3 |
| 22 | `22_FEATURE_TO_CODE_MAP.pdf` | **Feature-to-Code Map.** Product capabilities mapped to UI, API, runtime, storage, and tests. | 4 |
| 23 | `23_MASTER_INDEX.pdf` | **Master Index.** Portfolio-wide topic, package, endpoint, node, feature, and file index. | 14 |

The generator enforces that `docs/pdf/` contains exactly these 24 valid PDF files.

## Companion feature inventory

- [`EURSKEM_AI_FEATURE_CATALOG.md`](EURSKEM_AI_FEATURE_CATALOG.md) and
  [`EURSKEM_AI_FEATURE_CATALOG.pdf`](EURSKEM_AI_FEATURE_CATALOG.pdf) — complete
  current feature inventory covering product surfaces, Chat experiences,
  workflow/runtime capabilities, Knowledge/RAG, integrations, artifacts,
  security, evaluation, cost, operations, tests, and registered node types.
