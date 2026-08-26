# Eurskem AI — Complete Feature Catalog

**Repository:** `agentic-workflow-platform`  
**Catalog date:** 24 August 2026  
**Source basis:** current React routes and components, FastAPI routers, workflow runtime, registered node types, Chat workspace presets, Knowledge Studio, integrations, tests, and deployment configuration.

This document lists the features currently represented in the Eurskem AI codebase. It separates visible product features from reusable platform capabilities. A configured external provider may still be required for a feature to operate in a particular deployment.

## Status notation

- **Current** — represented by current product or backend code.
- **Configuration-dependent** — current implementation exists, but credentials or infrastructure must be configured.
- **Compatibility** — retained for saved-workflow compatibility but not preferred for new authoring.
- **Not current** — explicitly absent, retired, or deferred.

## At-a-glance product surfaces

| Surface | Main purpose | Primary implementation |
|---|---|---|
| Chat | Conversational use of workflows, sources, tools, and artifacts | `ui/src/modes/studio/business-chat/`, `app/api/chat_workspace.py`, `app/api/run_chat.py` |
| Workflows | Discover, inspect, import, generate, prepare, and launch workflows | `ui/src/modes/studio/Library.tsx`, `app/api/workflows.py` |
| Builder | Visual and YAML workflow authoring | `ui/src/modes/studio/Builder.tsx`, `app/api/builder.py` |
| Cockpit | Live technical workflow execution view | `ui/src/modes/studio/Cockpit.tsx`, `app/runtime/`, `app/api/runs.py` |
| Run History | Durable run inspection, retry, diagnosis, and Ask AI | `ui/src/modes/studio/RunHistory.tsx`, `app/workflow/run_history.py` |
| Knowledge Studio | Collection, ingestion, retrieval, profile, RAG-agent, and trace management | `ui/src/modes/knowledge/`, `app/api/knowledge.py` |
| Evaluation Lab | Golden-set scoring and model comparison | `ui/src/modes/eval/EvalRoot.tsx`, `app/api/eval.py` |
| Cost Management | Usage, pricing, infrastructure, cache, and budget administration | `ui/src/modes/cost/CostRoot.tsx`, `app/api/cost*.py` |

---

# 1. Authentication, shell, and navigation

| ID | Feature | Status | Implementation |
|---|---|---|---|
| AUTH-01 | Username/password sign-in | Current | `ui/src/components/auth/LoginPage.tsx`, `app/api/auth.py` |
| AUTH-02 | HttpOnly-cookie authentication | Current | `app/api/auth.py`, `app/security/jwt_handler.py` |
| AUTH-03 | Session rehydration after browser refresh | Current | `ui/src/App.tsx`, `GET /auth/me` |
| AUTH-04 | Session-expiry detection with a clear re-login notice | Current | `ui/src/App.tsx`, `ui/src/api/client.ts` |
| AUTH-05 | Logout and cookie invalidation | Current | `POST /auth/logout` |
| AUTH-06 | Role- and permission-based API access | Current | `app/security/dependencies.py`, `app/security/rbac.py` |
| SHELL-01 | Responsive application shell | Current | `ui/src/App.tsx`, layout components |
| SHELL-02 | Collapsible desktop sidebar | Current | `ui/src/App.tsx`, `Sidebar.tsx` |
| SHELL-03 | Mobile navigation drawer | Current | `ui/src/App.tsx`, `Sidebar.tsx` |
| SHELL-04 | Lazy-loaded top-level modes | Current | `ui/src/App.tsx` |
| SHELL-05 | Global run-cost display in the top bar | Current | `RunCostContext`, `Topbar.tsx` |
| SHELL-06 | Four top-level modes: Studio, Knowledge, Evaluation, Cost | Current | `ui/src/App.tsx` |

# 2. Workflow-powered Chat

| ID | Feature | Status | Implementation |
|---|---|---|---|
| CHAT-01 | Source-first conversational workspace | Current | `SourceFirstChatHome.tsx`, `BusinessChat.tsx` |
| CHAT-02 | General AI conversation through managed workflow adapters | Current | `chat_workspace_planner.py` |
| CHAT-03 | Execute an existing saved workflow from Chat | Current | `build_existing_workflow_adapter()` |
| CHAT-04 | Persist private Chat workflows | Current | `app/workflow/chat_workflow_store.py` |
| CHAT-05 | Shared and private workflow conversations | Current | `chat_conversation_store.py`, Chat routes |
| CHAT-06 | Conversation/session history panel | Current | `ChatSessionsPanel.tsx` |
| CHAT-07 | Follow-up turns in the same workflow session | Current | `BusinessChat.tsx`, run-chat APIs |
| CHAT-08 | Follow-up turns retain expanded document context | Current | `app/api/run_chat.py`, file adapters |
| CHAT-09 | Unified Add Sources hub | Current | `ChatWorkspaceOverlays.tsx`, `ComposerMenu.tsx` |
| CHAT-10 | Local file uploads as Chat sources | Current | workflow-input-file APIs |
| CHAT-11 | Pasted images uploaded as sources | Current | Chat composer/source model |
| CHAT-12 | Web URLs as explicit sources | Current | Chat source model and planner |
| CHAT-13 | Google Drive OAuth and account browsing | Configuration-dependent | `app/integrations/files/google_drive.py`, `integration_oauth.py` |
| CHAT-14 | Google Drive search, multi-select, and secure import | Configuration-dependent | `CloudFileBrowser.tsx`, file integration service |
| CHAT-15 | OneDrive integration framework | Configuration-dependent | `app/integrations/files/onedrive.py` |
| CHAT-16 | Imported cloud-file provenance | Current | file integration metadata and source model |
| CHAT-17 | Knowledge collection as a Chat source | Current | `NotebookSourcesPanel.tsx`, planner |
| CHAT-18 | Individual Knowledge-document selection | Current | `document_ids` planning and retrieval scope |
| CHAT-19 | Add and remove sources before or during a conversation | Current | source panels and workspace model |
| CHAT-20 | Source state restoration on desktop and mobile | Current | `chatWorkspaceStorage.ts` |
| CHAT-21 | Automatic routing among LLM, files, vision, web, retrieval, integration, workflow, and artifact plans | Current | `plan_workspace()` |
| CHAT-22 | Web-search route for current/public information | Current | web adapter and `WebSearchAgent` |
| CHAT-23 | Image and mixed-document analysis | Current | vision and multimodal file adapters |
| CHAT-24 | Image, diagram, infographic, PDF, DOCX, and PPTX output routing | Current | artifact adapters |
| CHAT-25 | Scientific skill selection and execution | Current | `ScientificSkillAgent`, research skills API |
| CHAT-26 | Prompt-template library | Current | `PromptTemplateLibrary.tsx`, prompt-template APIs |
| CHAT-27 | Speech input support | Browser-dependent | `speech.ts` |
| CHAT-28 | Live agent activity groups and step cards | Current | `AgentActivityGroup.tsx`, `AgentActivityCard.tsx` |
| CHAT-29 | Chat node inspector | Current | `ChatNodeInspector.tsx` |
| CHAT-30 | Human intervention/approval cards inside Chat | Current | `ChatInterventionCard.tsx` |
| CHAT-31 | Run controls from Chat | Current | `RunControlBar.tsx`, run-control model |
| CHAT-32 | Workflow context panel | Current | `WorkflowContextPanel.tsx` |
| CHAT-33 | Session audit panel | Current | `SessionAuditPanel.tsx` |
| CHAT-34 | Downloadable structured and file outputs | Current | `chatOutputs.ts`, artifact download routes |
| CHAT-35 | Retry malformed empty model answers such as `null`, `[]`, and `{}` | Current | `app/api/run_chat.py` |
| CHAT-36 | User-facing fallback when a valid answer still cannot be extracted | Current | `chatOutputs.ts` |
| CHAT-37 | Retrieval-timeout retry with checkpoint-backed recovery | Current | runtime/retrieval retry paths |
| CHAT-38 | Managed adapter caching by semantic source scope | Current | `app/api/chat_workspace.py` |

## 2.1 Chat experience presets

The planner currently exposes these 20 guided experiences:

1. Ask Questions About My Documents
2. Research Analyst
3. Research to Presentation
4. Research to PDF Report
5. Meeting / Interview Intelligence
6. Customer Feedback Analysis
7. Competitive Intelligence
8. Contract / Policy Understanding
9. Long Document Assistant
10. Study / Learning Assistant
11. Executive Brief Generator
12. Data / Results Interpreter
13. Product Requirements Assistant
14. Content Repurposing
15. Proposal Generator
16. Due-Diligence Assistant
17. Incident / Troubleshooting Assistant
18. Decision Support
19. Chat → Workflow Execution
20. Multi-Workflow AI Project

Source: `app/workflow/chat_workspace_planner.py`.

# 3. Workflow Library

| ID | Feature | Status | Implementation |
|---|---|---|---|
| LIB-01 | Browse canonical workflows by outcome | Current | `Library.tsx` |
| LIB-02 | Search workflows | Current | library toolbar/filter utilities |
| LIB-03 | Category navigation and category counts | Current | `CategoryNav.tsx`, `categories.ts` |
| LIB-04 | Filter by workflow characteristics and outputs | Current | `filters.ts`, `LibraryToolbar.tsx` |
| LIB-05 | Sort workflows | Current | library filters/sort model |
| LIB-06 | Grid and list presentation | Current | `LibraryToolbar.tsx` |
| LIB-07 | Favorite workflows locally | Current | `library/localState.ts` |
| LIB-08 | Recently opened and “continue where you left off” | Current | `library/localState.ts` |
| LIB-09 | Workflow detail panel | Current | `WorkflowDetailsPanel.tsx` |
| LIB-10 | Overview, requirements, outputs, stages, evidence, versions, runs, and technical detail tabs | Current | `library/tabs/` |
| LIB-11 | Readiness and preflight summary | Current | `library/readiness.ts` |
| LIB-12 | Prepare workflow inputs before launch | Current | `PrepareAndRunPanel.tsx` |
| LIB-13 | Launch a workflow into Cockpit | Current | workflow run APIs |
| LIB-14 | Import workflow YAML | Current | `ImportWorkflowDialog.tsx` |
| LIB-15 | Generate workflow YAML from a prompt | Current | `GenerateWorkflowDialog.tsx`, workflow-generation API |
| LIB-16 | Create a blank workflow | Current | Builder navigation |
| LIB-17 | Delete eligible workflows with confirmation | Current | `ConfirmDeleteDialog.tsx` |
| LIB-18 | Refresh workflow catalog | Current | `Library.tsx` |

# 4. Visual Workflow Builder

| ID | Feature | Status | Implementation |
|---|---|---|---|
| BUILD-01 | Visual graph editing with React Flow | Current | `Builder.tsx` |
| BUILD-02 | Registry-driven node palette | Current | `NodePalette.tsx`, node-type API |
| BUILD-03 | Searchable node/task palette | Current | `NodeSearchPalette.tsx` |
| BUILD-04 | Drag/drop node creation and edge editing | Current | `Builder.tsx` |
| BUILD-05 | Typed schema-generated configuration forms | Current | `SchemaForm.tsx` |
| BUILD-06 | Purpose-built editors for specialized nodes | Current | `ConfigPanel.tsx`, Builder inspector components |
| BUILD-07 | Workflow input editor | Current | `WorkflowInputsPanel.tsx` |
| BUILD-08 | Workflow variable editor | Current | `WorkflowVariablesPanel.tsx` |
| BUILD-09 | Visual input/output data mapping | Current | `DataMappingPanel.tsx` |
| BUILD-10 | YAML-to-canvas and canvas-to-YAML round trip | Current | `yaml-bridge.ts` |
| BUILD-11 | Direct YAML editing and validation | Current | Builder/YAML bridge |
| BUILD-12 | Zero-token preflight panel | Current | `PreflightPanel.tsx`, preflight API |
| BUILD-13 | Workflow autofix | Current | Builder autofix APIs |
| BUILD-14 | Node-level autofix | Current | Builder node-autofix path |
| BUILD-15 | Single-node test execution | Current | `BuilderTestPanel.tsx`, `/api/builder/node-test` |
| BUILD-16 | Branch/graph simulation | Current | `/api/builder/simulate` |
| BUILD-17 | Schema preview and output-contract tools | Current | Builder API |
| BUILD-18 | AI-assisted schema drafting | Current | `/api/builder/assist/schema` |
| BUILD-19 | AI-assisted rule drafting | Current | `/api/builder/assist/rules` |
| BUILD-20 | Prompt-drafting assistant | Current | `PromptDraftAssistant.tsx` |
| BUILD-21 | Ask AI about a node type | Current | `NodeTypeAskAi.tsx`, node-types Chat API |
| BUILD-22 | LLM model selector and routing policy | Current | `ModelSelect.tsx` |
| BUILD-23 | MCP server/tool discovery | Configuration-dependent | Builder MCP routes |
| BUILD-24 | MCP tool health and test execution | Configuration-dependent | Builder MCP routes |
| BUILD-25 | Email connection discovery and OAuth | Configuration-dependent | Builder email APIs |
| BUILD-26 | File/cloud integration discovery and OAuth | Configuration-dependent | integration OAuth APIs |
| BUILD-27 | Autosave serialized to prevent stale writes | Current | `Builder.tsx` |
| BUILD-28 | Recover autosaved drafts | Current | Builder draft store |
| BUILD-29 | Immutable workflow versions and version history | Current | `VersionHistoryPanel.tsx` |
| BUILD-30 | Save As workflow | Current | `SaveAsDialog.tsx` |
| BUILD-31 | Undo/redo | Current | Builder snapshots |
| BUILD-32 | Add non-executable notes to the canvas | Current | `NoteNode.tsx` |
| BUILD-33 | Automatic graph layout | Current | `flow-layout.ts` |
| BUILD-34 | Left-to-right and top-to-bottom layouts | Current | Builder view state |
| BUILD-35 | Stage grouping, bands, collapse, and placeholders | Current | `builder/stage-view.ts` |
| BUILD-36 | Semantic zoom and compact node rendering | Current | Builder zoom tiers |
| BUILD-37 | Fullscreen/expanded canvas | Current | `Builder.tsx` |
| BUILD-38 | Minimap, controls, and graph navigation | Current | React Flow and navigation utilities |
| BUILD-39 | Export workflow graph to SVG | Current | `graph-export.ts` |
| BUILD-40 | Export workflow graph to PNG | Current | `graph-export.ts` |
| BUILD-41 | Launch current draft for execution | Current | `RunDialog.tsx` |
| BUILD-42 | Reference pruning and rename safety when graph nodes change | Current | `builder-graph.ts` |

# 5. Workflow runtime and execution

| ID | Feature | Status | Implementation |
|---|---|---|---|
| RUN-01 | Typed YAML workflow schema | Current | `app/runtime/schema.py` |
| RUN-02 | LangGraph compilation | Current | `app/runtime/compiler.py` |
| RUN-03 | Zero-token schema and preflight validation | Current | `app/runtime/preflight.py` |
| RUN-04 | Graph topology validation | Current | runtime preflight modules |
| RUN-05 | Template/reference validation | Current | `templating.py`, `logic_preflight.py` |
| RUN-06 | Typed workflow inputs and variables | Current | runtime schema |
| RUN-07 | Typed node configuration, input, and output schemas | Current | `NodeType` contract |
| RUN-08 | Runtime output validation after every node | Current | node/runtime boundary |
| RUN-09 | Sequential execution | Current | graph edges |
| RUN-10 | Parallel fan-out through graph edges | Current | compiler/runtime |
| RUN-11 | Conditional branch routing | Current | `RouterAgent`, conditional edges |
| RUN-12 | Deterministic business-rule decisions | Current | `DecisionAgent` |
| RUN-13 | Human-in-the-loop pause, approve, reject, and edit | Current | `HumanInLoopAgent`, HITL runtime |
| RUN-14 | Durable resume after human intervention | Current | `app/runtime/hitl.py` |
| RUN-15 | Cooperative pause requests | Current | runtime/run store |
| RUN-16 | Run cancellation handling | Current | run APIs/runtime |
| RUN-17 | Checkpoint-backed retry of failed runs | Current | retry checkpoint APIs |
| RUN-18 | Reuse successful upstream node outputs during retry | Current | checkpoint runtime |
| RUN-19 | Child/subworkflow execution | Current | `SubprocessAgent` |
| RUN-20 | Durable run records | Current | `app/workflow/run_history.py` |
| RUN-21 | Node lifecycle timing and status | Current | runtime and run history |
| RUN-22 | Audit records | Current | audit APIs/store |
| RUN-23 | Per-node cost context | Current | LLM gateway/cost ledger |
| RUN-24 | Background run ownership and orchestration | Current | `BackgroundRunManager` |
| RUN-25 | Redis ownership leases | Configuration-dependent | `app/runtime/coordination.py` |
| RUN-26 | LangGraph Redis checkpoints with durable fallback | Configuration-dependent | app lifespan/HITL |
| RUN-27 | Idempotent external-operation ledger | Current | `app/integrations/operations.py` |

# 6. Cockpit and live execution monitoring

| ID | Feature | Status | Implementation |
|---|---|---|---|
| LIVE-01 | Live workflow graph | Current | `Cockpit.tsx` |
| LIVE-02 | Node statuses: pending, running, paused, completed, rejected, failed, skipped/cancelled | Current | Cockpit state model |
| LIVE-03 | Server-Sent Events progress stream | Current | `RunEventBus`, run events API |
| LIVE-04 | Redis-backed event replay across workers | Configuration-dependent | `app/runtime/events.py` |
| LIVE-05 | Manual authenticated stream client | Current | `ui/src/api/client.ts` |
| LIVE-06 | Auto-follow active node | Current | `Cockpit.tsx` |
| LIVE-07 | Active execution-path highlighting | Current | `cockpit-state.ts` |
| LIVE-08 | Stage grouping and collapse | Current | Cockpit graph-collapse modules |
| LIVE-09 | Show only active path | Current | `Cockpit.tsx` |
| LIVE-10 | Fullscreen graph and output views | Current | `Cockpit.tsx` |
| LIVE-11 | Resizable overview and inspector panels | Current | `useResizablePanel.ts` |
| LIVE-12 | Node overview, input, output, errors, logs, and technical inspection | Current | `NodeInspector.tsx`, Cockpit tabs |
| LIVE-13 | Human-review panel | Current | `HITLPanel.tsx` |
| LIVE-14 | Run output viewer and artifact download | Current | `OutputViewer.tsx` |
| LIVE-15 | Live run cost summary | Current | Cockpit hook and cost API |
| LIVE-16 | Stream failure and reconnect handling | Current | `useCockpitRun.ts` |

# 7. Run History and diagnosis

| ID | Feature | Status | Implementation |
|---|---|---|---|
| HIST-01 | Durable run list and run selection | Current | `RunListPanel.tsx` |
| HIST-02 | Run overview | Current | `OverviewTab.tsx` |
| HIST-03 | Node-by-node history | Current | `NodesTab.tsx` |
| HIST-04 | Inputs view | Current | `InputsTab.tsx` |
| HIST-05 | Outputs and artifact view | Current | `OutputsTab.tsx` |
| HIST-06 | Timeline and audit view | Current | `TimelineTab.tsx` |
| HIST-07 | Error diagnosis view | Current | `ErrorsTab.tsx` |
| HIST-08 | Ask AI about a completed/failed run | Current | `AskAiPanel.tsx`, run-chat API |
| HIST-09 | Historical node inspector | Current | Cockpit `NodeInspector` reused in Run History |
| HIST-10 | Retry failed runs from checkpoints | Current | Run History data hook and retry API |
| HIST-11 | Autofix failed workflow and reopen Builder | Current | Run History data hook |
| HIST-12 | Open a run in Cockpit | Current | Run History navigation |
| HIST-13 | Open proposal review from a run | Current | Run History navigation |
| HIST-14 | Open evidence candidates from a run | Current | Run History navigation |
| HIST-15 | Resizable and fullscreen inspection | Current | Run History panels |

# 8. Knowledge Studio, ingestion, and retrieval

| ID | Feature | Status | Implementation |
|---|---|---|---|
| KNOW-01 | Create and manage Knowledge collections | Current | Collections page/API |
| KNOW-02 | Collection-scoped UI context | Current | `CollectionContext.tsx` |
| KNOW-03 | Document upload and ingestion | Current | `IngestionPage.tsx`, ingestion APIs |
| KNOW-04 | Parser profiles | Current | Knowledge models/strategies |
| KNOW-05 | Standard, layout-aware, structure-aware, OCR-fallback, and vision-augmented parsing strategies | Current | `app/knowledge/models.py` |
| KNOW-06 | Chunking profiles and strategies | Current | Knowledge models/chunker |
| KNOW-07 | Embedding profiles | Current | Knowledge models/embedders |
| KNOW-08 | Document and index inventory | Current | `DocumentsIndexesPage.tsx` |
| KNOW-09 | Index creation and management | Current | Knowledge APIs/services |
| KNOW-10 | Retrieval Playground | Current | `PlaygroundPage.tsx` |
| KNOW-11 | Dense retrieval | Current | retrieval profile strategy |
| KNOW-12 | Sparse retrieval | Current | retrieval profile strategy |
| KNOW-13 | Hybrid retrieval | Current | retrieval service |
| KNOW-14 | Hybrid retrieval with reranking | Current | retrieval service/reranker |
| KNOW-15 | Retrieval-result comparison | Current | `/api/retrieval/compare` |
| KNOW-16 | Metadata filter validation and compilation | Current | `app/retrieval/filters.py` |
| KNOW-17 | Mandatory tenant/session isolation | Current | retrieval filters |
| KNOW-18 | Collection security scope | Current | retrieval models/filter compiler |
| KNOW-19 | Server-resolved document-level security scope | Current | `document_ids` retrieval path |
| KNOW-20 | Date, type, industry, and collection filters | Current | retrieval models |
| KNOW-21 | Self-query metadata filtering without security-scope override | Current | retrieval filter compiler |
| KNOW-22 | Retrieval routing profiles: deterministic, AI, and hybrid | Current | Knowledge models |
| KNOW-23 | Generation profiles | Current | Knowledge models |
| KNOW-24 | Retrieval Profiles management | Current | `ProfilesAgentsPage.tsx` |
| KNOW-25 | RAG Agent creation, listing, inspection, and querying | Current | RAG Agent APIs |
| KNOW-26 | Retrieval traces | Current | `TracesPage.tsx`, trace APIs |
| KNOW-27 | Open a retrieval trace from another product surface | Current | browser events/local storage handoff |
| KNOW-28 | Retrieval candidates, rerank reasons, latency, and provenance | Current | retrieval result contracts |
| KNOW-29 | Citation-grounded generation | Current | RAG service and nodes |
| KNOW-30 | Knowledge retrieval without answer generation | Current | `KnowledgeRetrieval` node |
| KNOW-31 | Retrieval cache identity scoped by selected documents | Current | RAG/Chat adapter cache identity |

# 9. AI, LLM, and model routing

| ID | Feature | Status | Implementation |
|---|---|---|---|
| AI-01 | Multi-provider LLM gateway | Configuration-dependent | `app/llm/` |
| AI-02 | OpenAI provider support | Configuration-dependent | OpenAI SDK/client |
| AI-03 | Anthropic provider support | Configuration-dependent | Anthropic SDK/client |
| AI-04 | OpenRouter provider support | Configuration-dependent | registry/OpenRouter modules |
| AI-05 | Local-model capability probes | Configuration-dependent | app lifespan/LLM registry |
| AI-06 | Model registry and aliases | Current | `app/llm/registry.py` |
| AI-07 | Capability-aware model selection | Current | model router |
| AI-08 | Automatic model routing | Current | model router/OpenRouter ranking |
| AI-09 | Accuracy-priority routing policies | Current | model routing policy |
| AI-10 | Retry with provider `Retry-After` handling | Current | LLM gateway |
| AI-11 | Ordered fallback chains | Current | LLM gateway/router |
| AI-12 | Intended-versus-actual model audit | Current | cost ledger |
| AI-13 | Structured-output generation and validation | Current | AI/Transform nodes |
| AI-14 | Configurable AI extraction, classification, summarization, translation, rewriting, generation, and analysis | Current | AI/Transform modes |
| AI-15 | Prompt caching metrics and management | Provider-dependent | Cost Management cache tab |
| AI-16 | LLM policy evaluation | Configuration-dependent | `app/security/llm_policy.py` |
| AI-17 | Fail-closed policy behavior | Current | security policy code |
| AI-18 | Kimi vision support | Configuration-dependent | `KimiVisionAgent` |
| AI-19 | OpenAI image generation | Configuration-dependent | `OpenAIImageGenerationAgent` |
| AI-20 | General image-generation service abstraction | Configuration-dependent | image tool/service |

# 10. Search, research, and scientific capabilities

| ID | Feature | Status | Implementation |
|---|---|---|---|
| RES-01 | Web search | Configuration-dependent | `WebSearchAgent`, web service |
| RES-02 | Scholarly candidate discovery | Configuration-dependent | scholarly discovery node/MCP |
| RES-03 | Bounded multi-query deep research | Configuration-dependent | `BoundedDeepResearchAgent` |
| RES-04 | Scientific research planning | Current | `ScientificResearchPlannerAgent` |
| RES-05 | Approved scientific skill catalog | Current | research skills API |
| RES-06 | Scientific skill execution | Current | `ScientificSkillAgent` |
| RES-07 | PaperQA evidence synthesis | Configuration-dependent | `PaperQAEvidenceSynthesizerAgent` |
| RES-08 | Prior-project retrieval from official project records | Configuration-dependent | `PriorProjectRetrieverAgent` |
| RES-09 | Internal project evidence retrieval | Configuration-dependent | `InternalProjectEvidenceRetrieverAgent` |
| RES-10 | Structured dataset retrieval | Configuration-dependent | `StructuredDatasetRetrieverAgent` |
| RES-11 | Research source acquisition and immutable storage | Configuration-dependent | `ResearchSourceAcquirer` |
| RES-12 | Citation registry construction | Current | `CitationRegistryBuilder` |
| RES-13 | Claim-to-source evidence verification | Current | `ClaimEvidenceVerifier` |
| RES-14 | Evidence candidate browser | Current | `RunCandidates.tsx`, candidates API |
| RES-15 | Verify an individual claim from candidate evidence | Current | candidates API |

# 11. Integrations, MCP, email, files, and external actions

| ID | Feature | Status | Implementation |
|---|---|---|---|
| INT-01 | MCP server registry | Configuration-dependent | `app/mcp/registry.py`, `connections.py` |
| INT-02 | Dynamic MCP tool discovery and JSON schemas | Configuration-dependent | MCP service and Builder |
| INT-03 | Direct call of one MCP tool | Configuration-dependent | `MCPToolAgent` |
| INT-04 | LLM-driven MCP tool loop | Configuration-dependent | `MCPAgent` |
| INT-05 | Read/write policy classification for MCP tools | Current | `app/mcp/policy.py` |
| INT-06 | MCP health checks | Configuration-dependent | Builder MCP API |
| INT-07 | Microsoft Dynamics 365 CRM/Dataverse connector | Configuration-dependent | `app/mcp/dynamics/` |
| INT-08 | Dynamics mock/demo backend | Current | Dynamics MCP configuration |
| INT-09 | Dynamics 365 Finance & Supply Chain connector | Configuration-dependent | `app/mcp/d365_finance/` and MCP server |
| INT-10 | Business-records MySQL connector | Configuration-dependent | `app/mcp/business_records/` |
| INT-11 | Guarded read-only SQL helpers | Configuration-dependent | business-record SQL guard |
| INT-12 | Email service abstraction | Configuration-dependent | `app/integrations/email/` |
| INT-13 | Gmail OAuth/email adapter | Configuration-dependent | `gmail.py`, OAuth service |
| INT-14 | Microsoft Graph email adapter | Configuration-dependent | `msgraph.py` |
| INT-15 | In-memory email adapter for testing/demo | Current | `memory.py` |
| INT-16 | Email connection management | Configuration-dependent | connection store and Builder APIs |
| INT-17 | Secure OAuth token vault for email | Configuration-dependent | email token vault |
| INT-18 | Google Drive file connector | Configuration-dependent | files integration package |
| INT-19 | OneDrive file connector | Configuration-dependent | files integration package |
| INT-20 | Secure OAuth token vault for cloud files | Configuration-dependent | files token vault |
| INT-21 | Generic integration node | Configuration-dependent | `IntegrationAgent` |
| INT-22 | Generic external REST/webhook action | Current, policy-dependent | `ExternalActionAgent` |
| INT-23 | URL validation/SSRF guard for external calls | Current | `app/integrations/url_guard.py` |
| INT-24 | Idempotent write-operation handling | Current | operation ledger |
| INT-25 | Database lookup tool | Configuration-dependent | database lookup service/node |

# 12. File handling and artifact generation

| ID | Feature | Status | Implementation |
|---|---|---|---|
| FILE-01 | Protected workflow input uploads | Current | workflow input file APIs/MinIO |
| FILE-02 | Workflow-scoped file references | Current | `WorkflowFileRef` contracts |
| FILE-03 | PDF text extraction | Current | `PDFTextExtractor` |
| FILE-04 | PDF, Office, Markdown, text, and code document loading | Current | `WorkflowFileLoader` |
| FILE-05 | Image references retained during document loading | Current | Workflow file loader |
| FILE-06 | Styled PDF generation | Current | `PDFProposalRenderer` |
| FILE-07 | Styled DOCX generation | Current | `DOCXProposalRenderer` |
| FILE-08 | Horizon-specific DOCX generation | Current | `HorizonDOCXProposalRenderer` |
| FILE-09 | Horizon-specific HTML/PDF proposal generation | Current | Horizon HTML renderer |
| FILE-10 | PowerPoint generation | Current | `PowerPointProposalSlides` |
| FILE-11 | Excel workbook generation | Current | `ExcelTool` |
| FILE-12 | Dynamic figure generation | Configuration-dependent | `DynamicFigureAgent` |
| FILE-13 | Figure embedding into outputs | Current | `FigureEmbedder` |
| FILE-14 | OpenAI-generated image artifact storage | Configuration-dependent | image-generation node |
| FILE-15 | Artifact metadata and authenticated downloads | Current | run/output APIs |
| FILE-16 | Graph image export from Builder | Current | SVG/PNG export |

# 13. Proposal engineering and evidence integrity

| ID | Feature | Status | Implementation |
|---|---|---|---|
| PROP-01 | Proposal workspace and truth graph | Current | `app/proposal_graph/` |
| PROP-02 | Proposal source/version registry | Current | proposal APIs/store |
| PROP-03 | Immutable evidence versions | Current | source-version APIs/storage |
| PROP-04 | Concept alternatives generation and judging | Current | `ConceptAlternativesAgent` |
| PROP-05 | Human-approved concept freeze | Current | `ConceptFreezeAgent` |
| PROP-06 | Call requirement coverage matrix | Current | `CallCoverageMatrixAgent` |
| PROP-07 | Methodology engineering and method cards | Current | `MethodologyEngineeringAgent` |
| PROP-08 | Proposal truth-graph normalization | Current | `GraphNormalizer` |
| PROP-09 | Proposal evidence factory | Current | `ProposalEvidenceFactoryAgent` |
| PROP-10 | Claim verification against exact passages | Current | evidence verifier/factory |
| PROP-11 | Cross-document consistency checking | Current | `ConsistencyChecker` |
| PROP-12 | Horizon proposal evaluation | Current | `HorizonEvaluationAgent` |
| PROP-13 | Submission-readiness gate | Current | `ProposalSubmissionGate` |
| PROP-14 | Human approval requests and decisions | Current | proposal approval APIs |
| PROP-15 | Proposal Review UI | Current | `ProposalReview.tsx` |
| PROP-16 | Render proposal to PDF | Current | proposal render API |
| PROP-17 | Render proposal to DOCX | Current | proposal DOCX API |
| PROP-18 | Render proposal slides | Current | PowerPoint node/workflows |

# 14. Evaluation Lab

| ID | Feature | Status | Implementation |
|---|---|---|---|
| EVAL-01 | Golden-set loading | Current | evaluation API/UI |
| EVAL-02 | LLM-as-a-Judge evaluation | Configuration-dependent | evaluation service |
| EVAL-03 | Faithfulness scoring | Current | evaluation criteria |
| EVAL-04 | Relevance scoring | Current | evaluation criteria |
| EVAL-05 | Completeness scoring | Current | evaluation criteria |
| EVAL-06 | Citation-accuracy scoring | Current | evaluation criteria |
| EVAL-07 | Scorecards and overall mean | Current | Evaluation Lab UI |
| EVAL-08 | Evaluation history | Current | evaluation API/store |
| EVAL-09 | Workflow model comparison | Configuration-dependent | compare API/UI |
| EVAL-10 | Per-model pass rate, cost, and latency | Current | comparison results |
| EVAL-11 | Per-case failures and checks | Current | Evaluation Lab UI |
| EVAL-12 | Recommended model selection | Current | comparison UI |

# 15. Cost Management

| ID | Feature | Status | Implementation |
|---|---|---|---|
| COST-01 | Per-run cost lookup | Current | `/api/cost/run/{run_id}` |
| COST-02 | Cost overview for a selected date range | Current | Cost Overview tab |
| COST-03 | Daily cost trend | Current | Cost Overview tab |
| COST-04 | Cost breakdown by model/provider/workflow/node | Current | cost admin API |
| COST-05 | Intended-versus-actual model cost attribution | Current | cost ledger |
| COST-06 | Direct/local model pricing catalog | Current | Pricing tab |
| COST-07 | OpenRouter pricing catalog | Configuration-dependent | pricing sync/API |
| COST-08 | Editable pricing overrides | Admin-only | Cost Pricing tab |
| COST-09 | Revert pricing overrides | Admin-only | Cost Pricing tab |
| COST-10 | Private infrastructure cost allocation | Admin-only | Private Infra tab |
| COST-11 | Prompt-cache summary | Provider-dependent | Prompt Cache tab |
| COST-12 | Budget creation and management | Admin-only | Budgets tab |
| COST-13 | Budget utilization/limits | Current | cost admin API |

# 16. Security and data protection

| ID | Feature | Status | Implementation |
|---|---|---|---|
| SEC-01 | Strict Pydantic request and workflow validation | Current | API/runtime schemas |
| SEC-02 | Resource ownership checks | Current | API routes/stores |
| SEC-03 | Tenant/session-scoped run visibility | Current | run APIs/store |
| SEC-04 | Tenant/session-scoped retrieval | Current | retrieval filter compiler |
| SEC-05 | Document-level retrieval authorization | Current | server-resolved `document_ids` |
| SEC-06 | Request size limiting | Current | security middleware |
| SEC-07 | Trusted-host middleware | Current | `app/main.py` |
| SEC-08 | Configurable CORS | Current | `app/main.py` |
| SEC-09 | Request correlation IDs | Current | request-context middleware |
| SEC-10 | Security response headers | Current | request-context middleware |
| SEC-11 | Redis-backed rate limits | Configuration-dependent | rate-limit middleware |
| SEC-12 | Production fail-closed rate limiting | Current | middleware policy |
| SEC-13 | Password hashing with Argon2 | Current | `app/security/passwords.py` |
| SEC-14 | JWT/token handling | Current | security tokens |
| SEC-15 | Entity/PII tokenization before external model use | Configuration-dependent | `EntityTokenizerService` |
| SEC-16 | OAuth state validation | Current | email/file OAuth APIs |
| SEC-17 | Encrypted/secured integration token vaults | Configuration-dependent | integration token vaults |
| SEC-18 | External URL and SSRF controls | Current | URL guard |
| SEC-19 | MCP write policy controls | Current | MCP policy |
| SEC-20 | Audit session retrieval | Current | audit API |

# 17. Observability, health, and operations

| ID | Feature | Status | Implementation |
|---|---|---|---|
| OPS-01 | Structured logging | Current | `app/observability/logging.py` |
| OPS-02 | Request, run, session, and node correlation | Current | logging/runtime context |
| OPS-03 | Prometheus metrics endpoint | Configuration-dependent | `/metrics` |
| OPS-04 | Liveness endpoint | Current | `/health` |
| OPS-05 | Dependency readiness endpoint | Current | `/ready` |
| OPS-06 | Per-service readiness latency | Current | health API |
| OPS-07 | MongoDB readiness | Configuration-dependent | health probes |
| OPS-08 | Redis readiness | Configuration-dependent | health probes |
| OPS-09 | MinIO readiness | Configuration-dependent | health probes |
| OPS-10 | Weaviate readiness | Configuration-dependent | health probes |
| OPS-11 | MCP readiness | Configuration-dependent | health probes |
| OPS-12 | Checkpointer readiness | Configuration-dependent | health probes |
| OPS-13 | Prometheus alert rules for availability, errors, latency, workflow failures, failovers, and rate limits | Current | `observability/prometheus/alerts.yml` |
| OPS-14 | Docker Compose development stack | Current | `docker-compose.yml` |
| OPS-15 | Production Compose stack | Current | `docker-compose.production.yml` |
| OPS-16 | Caddy TLS and production edge controls | Current | deployment configuration |
| OPS-17 | Immutable release deployment | Current | `deploy/ionos/deploy_release.sh` |
| OPS-18 | Readiness/smoke-gated deployment | Current | deploy script |
| OPS-19 | Automatic rollback on failed readiness | Current | deploy script |
| OPS-20 | Mongo/MinIO/Weaviate/Redis/workflow backup | Current | `deploy/ionos/backup.sh` |
| OPS-21 | Checksummed restore | Current | `deploy/ionos/restore.sh` |
| OPS-22 | Retired workflow-artifact cleanup | Current | cleanup deployment script |
| OPS-23 | Database migrations | Current | `app/db/migrations.py` |
| OPS-24 | Lease-locked, idempotent migration execution | Current | migration runtime |
| OPS-25 | CI backend/frontend/security/build gates | Current | `.github/workflows/` |
| OPS-26 | Live-provider smoke-test workflow | Configuration-dependent | live LLM workflow/scripts |

# 18. Testing and quality features

| ID | Feature | Status | Implementation |
|---|---|---|---|
| QA-01 | Backend unit and integration tests with Pytest | Current | `tests/` |
| QA-02 | Frontend unit/component tests with Vitest and Testing Library | Current | `ui/src/**/*.test.*` |
| QA-03 | Cross-browser Playwright tests | Current | `ui/e2e/` |
| QA-04 | Desktop, laptop, tablet, and mobile viewport matrices | Current | Playwright configuration/specs |
| QA-05 | Accessibility testing with axe-core | Current | Playwright dependency/tests |
| QA-06 | Workflow preflight of repository workflows without model tokens | Current | preflight scripts/tests |
| QA-07 | Node registry/audit tests | Current | node audit tests |
| QA-08 | Builder YAML round-trip tests | Current | `yaml-bridge.test.ts` |
| QA-09 | Security isolation tests | Current | security/retrieval/run tests |
| QA-10 | Deployment and smoke tests | Current | deployment/smoke test modules |
| QA-11 | Generated workflow QA matrix | Current | `scripts/build_workflow_qa_matrix.py` |
| QA-12 | Generated browser screenshots and release-gate reports | Current artifacts | `qa-results/` |

# 19. Current registered workflow node capabilities

The live node appendix below is generated from the current `NodeRegistry`-compatible source inventory. Each node is a reusable workflow capability; compatibility-only types may be hidden from new creation while remaining executable for saved workflows.

<!-- GENERATED_NODE_FEATURES_START -->
1. **`AITaskAgent`** — Deprecated — use TransformAgent's Inputs/Instructions/Outputs editor instead.  Category: `Uncategorized`. Source: `app/nodes/ai_task.py:424`.
2. **`BoundedDeepResearchAgent`** — Run multiple K-Dense-guided bounded research dossiers using a web-search tool-calling loop, with hard job, concurrency, and tool-call limits. Category: `Uncategorized`. Source: `app/nodes/bounded_deep_research_agent.py:117`.
3. **`CallCoverageMatrixAgent`** — Build a deterministic requirement-by-requirement Horizon call coverage matrix and submission gate. Category: `Uncategorized`. Source: `app/nodes/call_coverage.py:30`.
4. **`CitationRegistryBuilder`** — Deterministically reshape acquired full-text documents into a numbered, renderer-ready citation registry with canonical URLs folded into each formatted citation. Category: `Uncategorized`. Source: `app/nodes/citation_registry_builder.py:134`.
5. **`ClaimEvidenceVerifier`** — Verify each linked claim against an exact passage in an immutable source version. Category: `Uncategorized`. Source: `app/nodes/claim_evidence_verifier.py:62`.
6. **`ConceptAlternativesAgent`** — Generate conservative, balanced, and ambitious Horizon concepts grounded in the approved proposal graph. Category: `Uncategorized`. Source: `app/nodes/concept_alternatives.py:53`.
7. **`ConceptFreezeAgent`** — Resolve the human gate's concept decision against the three generated alternatives.  Category: `Uncategorized`. Source: `app/nodes/concept_freeze.py:62`.
8. **`ConsistencyChecker`** — Deterministic gate.  Category: `Uncategorized`. Source: `app/nodes/consistency_checker.py:149`.
9. **`DataTransformAgent`** — Deprecated — use TransformAgent's mode: deterministic instead.  Category: `Uncategorized`. Source: `app/nodes/data_transform.py:173`.
10. **`DecisionAgent`** — Deterministic business rules: IF/THEN over typed fields, with nested AND/OR/NOT.  Category: `Uncategorized`. Source: `app/nodes/decision.py:179`.
11. **`DOCXProposalRenderer`** — Render proposal sections to a styled .docx (corporate, professional, warm). Category: `Uncategorized`. Source: `app/nodes/docx_renderer.py:120`.
12. **`DynamicFigureAgent`** — Generate a diagram image for every [[IMAGE PROMPT: ...]] marker and embed it as a data URI the DOCX renderer can render; also emit a caption-only variant for text documents. Category: `Uncategorized`. Source: `app/nodes/dynamic_figure_agent.py:127`.
13. **`Echo`** — Renders a template string. Category: `Uncategorized`. Source: `app/nodes/_stubs.py:70`.
14. **`EmailAgent`** — Email in one capability: search, read, draft, reply or send, over Gmail or Microsoft — provider differences live in adapters. Category: `Uncategorized`. Source: `app/nodes/email_integration.py:229`.
15. **`EndAgent`** — What the workflow returns or shows when it finishes. Category: `Uncategorized`. Source: `app/nodes/end.py:109`.
16. **`ExcelTableExtractor`** — Extract tables from an .xlsx in object storage. Category: `Uncategorized`. Source: `app/nodes/excel_tool.py:44`.
17. **`ExternalActionAgent`** — Call an external REST API or send a webhook.  Category: `Uncategorized`. Source: `app/nodes/external_action.py:138`.
18. **`FigureEmbedder`** — Replace [[IMAGE PROMPT: marker]] placeholders with real embedded images read from object storage, before the proposal is rendered. Category: `Uncategorized`. Source: `app/nodes/figure_embedder.py:110`.
19. **`GraphNormalizer`** — LLM extracts structure from source text; pydantic validates it; valid objects are written to proposal_graph.  Category: `Uncategorized`. Source: `app/nodes/graph_normalizer.py:211`.
20. **`HorizonDOCXProposalRenderer`** — Convert proposal HTML or Markdown into an editable, citation-aware Horizon Europe Part B DOCX with native headings, TOC, tables, figures, page fields, page-limit estimate, and evidence annex. Category: `Uncategorized`. Source: `app/nodes/horizon_docx_renderer.py:111`.
21. **`HorizonEvaluationAgent`** — Score Excellence, Impact, and Implementation with independent cross-provider evaluators plus deterministic evidence gates. Category: `Uncategorized`. Source: `app/nodes/horizon_evaluation.py:42`.
22. **`HorizonHTMLProposalRenderer`** — Convert proposal HTML or Markdown into a citation-aware Horizon Europe Part B PDF with cover, TOC, page checks, and evidence annex. Category: `Uncategorized`. Source: `app/nodes/html_proposal_renderer.py:98`.
23. **`HumanInLoopAgent`** — Pause for human approval, rejection, or edit. Category: `Uncategorized`. Source: `app/nodes/human_in_loop.py:157`.
24. **`IntegrationAgent`** — Browse and pull files from a connected Google Drive or OneDrive account — provider differences live in adapters. Category: `Uncategorized`. Source: `app/nodes/integration.py:206`.
25. **`InternalProjectEvidenceRetrieverAgent`** — Retrieve partner, pilot, work-plan, budget and approved internal database facts; require an exact source passage and explicit human approval before drafting. Category: `Uncategorized`. Source: `app/nodes/internal_project_evidence_retriever.py:126`.
26. **`KimiVisionAgent`** — Analyse an uploaded image with Kimi K3.  Category: `Uncategorized`. Source: `app/nodes/kimi_vision_agent.py:70`.
27. **`KnowledgeRetrieval`** — Retrieve secured knowledge through a saved Retrieval Profile without generating an answer. Category: `Uncategorized`. Source: `app/nodes/knowledge_retrieval.py:90`.
28. **`Literal`** — Emits a literal config value as its output. Category: `Uncategorized`. Source: `app/nodes/_stubs.py:33`.
29. **`MCPAgent`** — LLM-driven agent loop using MCP tools. Category: `Uncategorized`. Source: `app/nodes/mcp_agent.py:86`.
30. **`MCPToolAgent`** — Call one capability on a connected MCP server — CRM, ERP, or any other business system.  Category: `Uncategorized`. Source: `app/nodes/mcp_tool.py:154`.
31. **`MethodologyEngineeringAgent`** — Produce one skill-guided Method Card per frozen-concept objective: method, baseline, validation, uncertainty handling, and failure condition, grounded only in verified claims and known research questions. Category: `Uncategorized`. Source: `app/nodes/methodology_engineering.py:129`.
32. **`MinIOEvidenceIngestion`** — Index acquired full-text sources (from MinIO pages.json) into Weaviate, stamping each chunk with its global citation display_number so retrieved passages carry their footnote number to the drafter. Category: `Uncategorized`. Source: `app/nodes/minio_evidence_ingestion.py:108`.
33. **`OpenAIImageGenerationAgent`** — Generate an image with an approved OpenAI image model and store the bytes in object storage. Category: `Uncategorized`. Source: `app/nodes/openai_image_generation_agent.py:89`.
34. **`PaperQAEvidenceSynthesizerAgent`** — Run PaperQA2 over already-acquired full-text documents, per claim, for per-document coverage and gap-aware literature synthesis.  Category: `Uncategorized`. Source: `app/nodes/paperqa_evidence_synthesizer.py:131`.
35. **`PDFProposalRenderer`** — Render proposal sections to a styled PDF (corporate, professional, warm). Category: `Uncategorized`. Source: `app/nodes/pdf_tool.py:132`.
36. **`PDFTextExtractor`** — Extract text from a .pdf in object storage. Category: `Uncategorized`. Source: `app/nodes/pdf_tool.py:50`.
37. **`PowerPointProposalSlides`** — Build a .pptx deck from proposal sections. Category: `Uncategorized`. Source: `app/nodes/powerpoint_tool.py:49`.
38. **`PriorProjectRetrieverAgent`** — Search official CORDIS, LIFE and EIP-AGRI project records for precedents and synergies.  Category: `Uncategorized`. Source: `app/nodes/prior_project_retriever.py:139`.
39. **`ProposalEvidenceFactoryAgent`** — Verify proposal claims against immutable full-text pages, build an auditable citation registry, and fail closed on evidence gaps. Category: `Uncategorized`. Source: `app/nodes/proposal_evidence_factory.py:351`.
40. **`ProposalSubmissionGate`** — Deterministically decide whether a proposal is ready for final human approval or autonomous document export. Category: `Uncategorized`. Source: `app/nodes/proposal_submission_gate.py:108`.
41. **`ProposalTruthGraphAgent`** — Freeze a drafting-safe truth graph containing only verified or qualified evidence links, plus explicit gaps and approval items. Category: `Uncategorized`. Source: `app/nodes/proposal_truth_graph.py:149`.
42. **`PythonSnippetAgent`** — Run a short Python snippet as a workflow step, in an isolated sandbox with no network access and no access to this platform's own code or secrets. Category: `Uncategorized`. Source: `app/nodes/python_snippet.py:100`.
43. **`RAGAgent`** — Hybrid retrieval + grounded answer with citations. Category: `Uncategorized`. Source: `app/nodes/rag.py:156`.
44. **`ResearchSourceAcquirer`** — Resolve and store bounded Deep Research citations as immutable HTML or PDF source versions for exact-passage verification. Category: `Uncategorized`. Source: `app/nodes/research_source_acquirer.py:100`.
45. **`RouterAgent`** — Branch the workflow on a field value, on business conditions, or via model judgment — with the reason recorded. Category: `Uncategorized`. Source: `app/nodes/router.py:254`.
46. **`ScientificResearchPlannerAgent`** — Turn the call, selected concept, and proposal graph into several bounded research briefs, each routed through approved K-Dense skills. Category: `Uncategorized`. Source: `app/nodes/scientific_research_planner.py:134`.
47. **`ScientificSkillAgent`** — Scientific synthesis or proposal work guided by approved K-Dense Agent Skills. Category: `Uncategorized`. Source: `app/nodes/scientific_skill_agent.py:67`.
48. **`SQLQueryAgent`** — Run a read-only SQL query against the business-records database, for a lookup the classified MCP tools don't already cover. Category: `Uncategorized`. Source: `app/nodes/sql_query.py:108`.
49. **`StartAgent`** — How this workflow begins: a business-friendly input form, or a conversational chatbot entry point. Category: `Uncategorized`. Source: `app/nodes/start.py:117`.
50. **`StructuredDatasetRetrieverAgent`** — Retrieve bounded Eurostat/official structured data with explicit filters, immutable snapshots, row hashes, count reconciliation, and auditable provenance.  Category: `Uncategorized`. Source: `app/nodes/structured_dataset_retriever.py:139`.
51. **`SubprocessAgent`** — Run another saved workflow as a reusable business subprocess.  Category: `Uncategorized`. Source: `app/nodes/subprocess.py:115`.
52. **`TextAssemblerAgent`** — Join: waits for multiple upstream branches (e.g.  Category: `Uncategorized`. Source: `app/nodes/text_assembler.py:64`.
53. **`TransformAgent`** — Pure LLM transform: summarize, classify, rewrite, extract. Category: `Uncategorized`. Source: `app/nodes/transform.py:301`.
54. **`WebSearchAgent`** — Search the live public web with Auto, Tavily, OpenAI, or Kimi K3.  Category: `Uncategorized`. Source: `app/nodes/web_search_agent.py:85`.
55. **`WorkflowFileLoader`** — Load uploaded workflow files.  Category: `Uncategorized`. Source: `app/nodes/workflow_file_loader.py:82`.
56. **`WorkflowInputAgent`** — Information entering the workflow: manual, API, document, email or a previous workflow's output, with a declared shape. Category: `Uncategorized`. Source: `app/nodes/workflow_input.py:264`.
<!-- GENERATED_NODE_FEATURES_END -->

# 20. Explicitly absent, retired, or deferred capabilities

These items should **not** be described as current Eurskem AI features:

| Capability | Current state |
|---|---|
| Multi-workflow Pipeline product/runtime | Retired from the current application and route surface |
| Business View product surface | Retired from the current application |
| Scheduled/time-based workflow triggers | Not registered |
| Generic webhook trigger node | Not registered |
| Generic Wait/Delay node | Not registered |
| Generic For Each/Loop node | Deferred; no first-class registered node |
| Generic Merge node | Not registered; graph fan-in and `TextAssemblerAgent` cover current needs |
| Generic scheduled automation service | Not present |
| WebSocket run streaming | Not used; authenticated SSE is the current mechanism |
| Redux/Zustand/React Query state layer | Not used; state is local/context/custom-hook based |
| Alertmanager/pager routing | Not currently configured in the production stack |
| Formal GDPR compliance | Not claimed; the platform provides technical controls, not organizational/legal compliance |

---

## Maintenance

Update this catalog when any of the following changes:

1. A top-level UI mode or Studio route is added or removed.
2. A FastAPI router or major resource lifecycle is added or removed.
3. A node type is registered, hidden, deprecated, or deleted.
4. A Chat experience preset or planner route is changed.
5. An integration/provider is added or retired.
6. A major runtime, security, deployment, or observability capability changes.

For lower-level code ownership and request mapping, see:

- `docs/pdf/05_FRONTEND_BACKEND_MAPPING.pdf`
- `docs/pdf/06_WORKFLOW_ENGINE_AND_NODE_TYPES.pdf`
- `docs/pdf/12_API_REFERENCE_AND_REQUEST_FLOWS.pdf`
- `docs/pdf/17_FILE_BY_FILE_CODE_REFERENCE.pdf`
- `docs/pdf/22_FEATURE_TO_CODE_MAP.pdf`
- `docs/pdf/23_MASTER_INDEX.pdf`