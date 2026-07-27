Architecture

Design goal

The repository is a reusable node-typed AI workflow platform. Domain workflowscan introduce their own schemas, rules, evidence models, and evaluations, butthe runtime must stay independent of any single use case.

The central boundary is:

Layer

Owns

Must not own

Platform core

execution, state channels, model routing, retrieval, tools, security, audit, cost, events

proposal, healthcare, sales, or logistics rules

Capability nodes

reusable actions such as transform, RAG, tool use, routing, HITL, and document generation

end-to-end business policy

Use-case pack

domain schemas, workflow YAML, prompts, deterministic rules, policies, and eval datasets

shared infrastructure clients

External adapter

APIs, databases, MCP servers, and model providers

workflow decisions

Runtime

flowchart TD
    UI["React Builder and Cockpit"] --> API["FastAPI"]
    API --> C["Workflow compiler"]
    C --> G["LangGraph execution"]
    G --> N["Typed capability nodes"]
    N --> S["Shared services"]
    G --> D["Namespaced domain state"]

app/runtime/compiler.py converts a validated WorkflowSpec into a LangGraphstate graph. Every node passes through the same runtime boundary, which appliestemplate resolution, per-node model/cost context, events, output validation, andaudit recording.

Stable workflow contract

app/runtime/schema.py defines one canonical WorkflowSpec:

use_case identifies the owning pack;

inputs define caller-supplied data;

static_variables hold workflow-controlled constants;

nodes and edges define execution;

selected_model overrides a node's model only when allowed;

output defines the caller-facing result;

graph references are validated before execution.

Invalid entries, exits, branches, and duplicate node IDs fail at load time.

State boundary

Shared state contains:

inputs and workflow variables;

node outputs;

append-only audit entries;

session and collection identity;

workflow metadata;

domain_state.

domain_state is a map keyed by a stable namespace:

domain_state["eu_proposal"]
domain_state["sales_strategy"]
domain_state["prior_authorization"]

Simple domains receive a recursive mapping merge. Complex packs register atyped reducer through DomainStateRegistry. The EU proposal pack registersmerge_graph, preserving its parallel-safe proposal knowledge graph withoutmaking the core import proposal models.

Shared services

app/main.py is the composition root. It builds and shares:

MongoDB for manifests, cost, evaluation, audit, and run history;

Weaviate for hybrid retrieval;

object storage for document bytes and generated artifacts;

Redis for cache and future durable execution;

the model gateway;

MCP client sessions;

the retrieval pipeline;

the event bus.

Nodes receive these services from the runtime. They do not create their ownprovider or database connections.

Included use cases

EU proposals

The advanced pack currently provides:

GraphNormalizer for typed extraction from a concept note;

EvidenceAgent for open scholarly discovery through MCP;

ConsistencyChecker for deterministic submission-readiness rules;

an eu_proposal domain-state namespace.

The target workflow is:

official call + concept note
  -> requirement coverage
  -> verified evidence
  -> concept alternatives
  -> selected idea
  -> methodology and impact pathway
  -> proposal skeleton
  -> independent evaluator review

Other workflow packs

Project Alex proposal generation, Miller Heiman sales strategy, and priorauthorization already exist as workflow definitions. Their next iterationshould add typed domain state and evaluation suites without changing the core.

Known gaps

Paused workflows use MemorySaver plus an in-process graph cache. A restartloses them.

Domain nodes are still imported from the global node package; explicituse-case manifests and lazy registration are the next boundary improvement.

External-service tests need a dedicated Docker test profile and host-safeconfiguration.

The frontend package lock is not currently reproducible with npm ci.

Service health is best-effort at startup; typed service contracts andreadiness failures are not yet enforced.

Prompt injection, per-data-class model policy, retries, rate limits, andproduction deployment configuration still need hardening.

Reference directions

Awesome AI for Scienceis used as a capability catalogue for evidence and research tooling, not as aruntime replacement.

AI Engineering from Scratchprovides engineering checklists for RAG, evaluation, MCP security,checkpoints, observability, cost, and production operations.