# AI-Native Chat Node Evaluation and Unified Sources Architecture

**Repository:** `agentic-workflow-platform` (Eurskem AI)  
**Analysis date:** 23 August 2026  
**Scope:** Active node implementations under `app/nodes/`, runtime behavior under
`app/runtime/` and `app/workflow/`, source and integration services, and the
current Business Chat projection under `ui/src/modes/studio/business-chat/`.

> **Evidence standard.** This report evaluates implementation, not node names.
> Claims are based on the current Python node contracts, runtime, API, and React
> code. Product recommendations are labelled as recommendations. No capability
> is described as present unless it was found in this checkout.

---

## Contents

1. [Executive summary](#1-executive-summary)
2. [What exists](#2-what-exists)
3. [Recommended node portfolio](#3-recommended-node-portfolio)
4. [Recommended nodes in detail](#4-recommended-nodes-in-detail)
5. [Node opportunity matrix](#5-node-opportunity-matrix)
6. [Unified Sources](#6-unified-sources)
7. [High-value node combinations](#7-high-value-node-combinations)
8. [Capabilities to keep hidden](#8-capabilities-to-keep-hidden)
9. [Existing capabilities reusable in Chat](#9-existing-capabilities-reusable-in-chat)
10. [Composer and automatic-selection policy](#10-composer-and-automatic-selection-policy)
11. [Gap analysis](#11-gap-analysis)
12. [Priority roadmap](#12-priority-roadmap)
13. [Recommended first validation experience](#13-recommended-first-validation-experience)
14. [Direct answers](#14-direct-answers)

---

# 1. Executive summary

The Builder has enough implemented capability to become a materially more
powerful AI Chat product without replacing the workflow engine. The opportunity
is to make a small set of nodes feel like native conversational capabilities
while suppressing most internal orchestration.

The most important existing nodes for Chat are:

1. `StartAgent` — chat, structured input, and attachments.
2. `TransformAgent` — configurable AI reasoning, typed output, and deterministic
   transformation.
3. `KnowledgeRetrieval` and `RAGAgent` — secured internal retrieval, provenance,
   and grounded answers.
4. `WebSearchAgent` — current public information.
5. `IntegrationAgent` — Google Drive and OneDrive discovery and download.
6. `WorkflowFileLoader` and `KimiVisionAgent` — document and image understanding.
7. `MCPToolAgent` and `SQLQueryAgent` — controlled enterprise-system and exact
   structured-data access.
8. `RouterAgent` and `DecisionAgent` — automatic orchestration and policy.
9. `HumanInLoopAgent` — durable approval, edit, and reject.
10. `EmailAgent` and `ExternalActionAgent` — safe real-world action.
11. `SubprocessAgent` — durable specialist delegation.
12. Renderer and image nodes — reusable artifacts rather than text alone.

The strongest missing product layer is not another retrieval node. The system
already accesses Web, internal knowledge, drives, uploads, PDFs, images,
research papers, email, MCP systems, SQL, and official datasets. What is missing
is a **unified source contract and orchestration adapter** that normalizes those
different outputs into one source experience with consistent provenance,
filters, citations, permissions, freshness, and conflict handling.

The Chat product should therefore follow this rule:

> Show results, decisions, required user actions, source attribution, artifacts,
> and actionable failures. Hide parsing, transforms, joins, indexing, routing
> mechanics, checkpoint mechanics, and routine tool operations.

---

# 2. What exists

## 2.1 Node inventory

The current `app/nodes/` implementation defines **57 registered node types**:

```text
AITaskAgent
BoundedDeepResearchAgent
CallCoverageMatrixAgent
CitationRegistryBuilder
ClaimEvidenceVerifier
ConceptAlternativesAgent
ConceptFreezeAgent
ConsistencyChecker
DOCXProposalRenderer
DataTransformAgent
DecisionAgent
DynamicFigureAgent
Echo
EmailAgent
EndAgent
ExcelTableExtractor
ExternalActionAgent
FigureEmbedder
GraphNormalizer
HorizonDOCXProposalRenderer
HorizonEvaluationAgent
HorizonHTMLProposalRenderer
HumanInLoopAgent
IntegrationAgent
InternalProjectEvidenceRetrieverAgent
KimiVisionAgent
KnowledgeRetrieval
Literal
MCPAgent
MCPToolAgent
MethodologyEngineeringAgent
MinIOEvidenceIngestion
OpenAIImageGenerationAgent
PDFProposalRenderer
PDFTextExtractor
PaperQAEvidenceSynthesizerAgent
PowerPointProposalSlides
PriorProjectRetrieverAgent
ProposalEvidenceFactoryAgent
ProposalSubmissionGate
ProposalTruthGraphAgent
PythonSnippetAgent
RAGAgent
ResearchSourceAcquirer
RouterAgent
SQLQueryAgent
ScholarlyCandidateDiscoveryAgent
ScientificResearchPlannerAgent
ScientificSkillAgent
StartAgent
StructuredDatasetRetrieverAgent
SubprocessAgent
TextAssemblerAgent
TransformAgent
WebSearchAgent
WorkflowFileLoader
WorkflowInputAgent
```

`Literal` and `Echo` are test/smoke utilities and have no product role in Chat.

The implementation itself marks these older nodes as superseded for new work:

- `WorkflowInputAgent` → `StartAgent`.
- `AITaskAgent` → `TransformAgent`.
- `DataTransformAgent` → `TransformAgent(mode: deterministic)`.

Compatibility must remain, but the deprecated types should not define the new
Chat mental model.

## 2.2 Runtime capabilities confirmed in code

| Capability | Current implementation |
|---|---|
| Typed graph execution | `WorkflowSpec`, `NodeType`, Pydantic config/output validation, LangGraph compiler |
| Parallel branches | Reducer-backed `WorkflowState.node_outputs`; graph fan-out |
| Multi-route | `RouterAgent(selection: multi)` |
| Deterministic join | `TextAssemblerAgent` waits for and combines referenced branches |
| Live events | Session-scoped SSE with replay and heartbeat in `app/runtime/events.py` |
| Event types | Node started/completed/reused/paused, run terminal state, model selection, LLM token |
| Durable run state | Inputs, outputs, node records, timing, errors, model selections, and saved YAML in `run_history` |
| Large-output persistence | GridFS externalization for large node/run values |
| HITL | LangGraph interrupt plus durable checkpoint and REST resume |
| Cooperative pause | Pause flag checked at the next node boundary |
| Retry | New run reuses completed node outputs and consumes no new tokens for reused nodes |
| Restart | New attempt from saved workflow and original validated inputs |
| Long-running work | Background run manager, subprocess child runs, pipeline stages |
| Model routing | Requested/actual model, fallback, cost, latency, and task-aware auto-selection |
| Source isolation | Session/owner/collection filters in retrieval and Knowledge Studio |
| File safety | Stable object references; bytes do not travel in graph state |
| External-action safety | Approval, policy classification, timeout, idempotency, ambiguous-failure handling |
| Sandboxed code | Network-isolated, non-root snippet runner with timeout/memory/output limits |

## 2.3 Source capabilities confirmed in code

| Source | Existing nodes/services | Provenance/freshness found |
|---|---|---|
| Uploaded files | `StartAgent`, workflow file API, `WorkflowFileLoader` | File ID, SHA-256, object key, MIME/category, extraction status |
| PDFs/documents/code | `WorkflowFileLoader`, `PDFTextExtractor` | Filename/object key; PDF pages in specialized extractor |
| Spreadsheets | `WorkflowFileLoader`, `ExcelTableExtractor` | File identity, sheets, rows |
| Uploaded images | `WorkflowFileLoader`, `KimiVisionAgent` | File identity, object key, MIME, visual analysis |
| Internal indexed knowledge | `KnowledgeRetrieval`, `RAGAgent` | Document/source/source-version/chunk/index IDs, page, section, scores, trace ID |
| Google Drive/OneDrive | `IntegrationAgent` | Connection, provider, file/folder ID, MIME, size, `modified_at`, Web URL |
| Public Web | `WebSearchAgent`, `BoundedDeepResearchAgent` | URL, title, snippet, score, provider, search audit |
| Research papers | Scholarly discovery/acquisition/evidence nodes | Authors, year, DOI, canonical IDs/URL, authority, retraction state, immutable full text |
| Official EU projects | `PriorProjectRetrieverAgent` | Canonical URL, source, authority, retrieved time |
| Official datasets | `StructuredDatasetRetrieverAgent` | Endpoint, params, access time, source version, unit, period, geography, response/snapshot hashes |
| Business systems | `MCPToolAgent`, `SQLQueryAgent` | Server/tool/operation, rows or typed records, duration, status; no common citation envelope |
| Email | `EmailAgent` | Provider, connection, message/thread IDs, sender, received time, attachments metadata |

---

# 3. Recommended node portfolio

## 3.1 Core AI Chat Nodes

The smallest useful first-class portfolio is:

- `StartAgent`
- `TransformAgent`
- `KnowledgeRetrieval`
- `RAGAgent`
- `RouterAgent`
- `HumanInLoopAgent`
- `EndAgent`

This set provides conversation, structured AI, grounded context, automatic
orchestration, human control, and a clean result boundary.

## 3.2 AI Amplification Nodes

- `WebSearchAgent`
- `IntegrationAgent`
- `WorkflowFileLoader`
- `KimiVisionAgent`
- `MCPToolAgent`
- `SQLQueryAgent`
- `DecisionAgent`
- `SubprocessAgent`
- `EmailAgent`
- `ExternalActionAgent`
- `TextAssemblerAgent`
- `OpenAIImageGenerationAgent`
- Generic PDF/DOCX/PowerPoint renderers

These add current information, enterprise context, multimodality, exact data,
delegation, safe action, parallel synthesis, and reusable artifacts.

## 3.3 Advanced AI Nodes

- `MCPAgent`
- `PythonSnippetAgent`
- `ScientificSkillAgent`
- Scientific planning/discovery/acquisition/evidence nodes
- Proposal Engineering nodes
- Horizon-specific renderers and gates

These enable sophisticated research, developer, scientific, and proposal
experiences but should not clutter general Chat.

---

# 4. Recommended nodes in detail

## 4.1 `StartAgent`

**What it does.** Starts a workflow in chatbot or typed-form mode. Chatbot mode
returns `message` and attachment references. Form mode supports typed fields and
file inputs.

**AI value.** It is the existing multimodal conversation boundary.

**Chat use.** It should be represented by the composer and contextual input
components, never as a visible “Start node” message.

**Example.** A user dictates a customer problem and attaches a PDF and image.

**Interaction.** Direct.

**Visibility.** Primary Chat capability.

**Priority.** P0.

## 4.2 `TransformAgent`

**What it does.** In AI mode, performs extraction, classification, analysis,
comparison, rewriting, or generation with typed outputs, model selection,
language policy, retries, and a routable `fail_on_error: false` path. In
deterministic mode, performs exact copy/format/join/coalesce/object/select/
number/boolean/string/count operations without a model.

**AI value.** This is the current general AI primitive. `AITaskAgent` is
explicitly deprecated in its favor.

**Chat use.** Show it as an agent answer, recommendation, comparison, structured
card, or editable draft when its result matters. Hide extraction and data-shape
steps.

**Example.** Extract account identity and intent into a typed schema, then later
synthesize internal, Web, and CRM evidence into one cited recommendation.

**Interaction.** Usually indirect; direct when it produces the answer/draft.

**Visibility.** Primary, contextual, or hidden according to output relevance.

**Priority.** P0.

## 4.3 `KnowledgeRetrieval`

**What it does.** Runs secured retrieval through a saved Collection and Retrieval
Profile. It returns retrieved chunks, citations, final context, resource IDs,
document/source-version provenance, page/section locators, timing, and trace ID.

**AI value.** Gives the assistant authoritative tenant-scoped internal context.

**Chat use.** One compact source activity, normally feeding a later synthesis:

```text
Searched internal knowledge · 8 passages · 4 documents
```

**Example.** Find the approved cancellation policy before drafting a customer
reply.

**Interaction.** Optional collection/profile scope; usually automatic.

**Visibility.** Contextual source activity and citations.

**Priority.** P0.

## 4.4 `RAGAgent`

**What it does.** Combines secured retrieval with grounded generation. It
supports saved RAG resources, metadata filters, hybrid retrieval, reranking,
compression, answer-model resolution, citations, and retrieval trace metadata.

**AI value.** It is the closest existing node to a complete grounded Chat answer.

**Chat use.** Normal cited response with expandable source details.

**Example.** Answer a policy question using only approved internal documents.

**Interaction.** Direct result; source scope may be user-constrained.

**Visibility.** Primary answer plus Sources panel.

**Priority.** P0.

Use `RAGAgent` when one saved internal corpus should answer directly. Use
`KnowledgeRetrieval` when internal context must be combined with Web, Drive,
database, attachment, image, or other outputs before synthesis.

## 4.5 `RouterAgent`

**What it does.** Routes by field, conditions, legacy rule, or model judgment.
It supports one or several routes and records reason, route value, condition
trace, and fallback usage.

**AI value.** It is the existing source/capability orchestration mechanism.

**Chat use.** Usually silent. Explain only meaningful choices or fallbacks.

**Example.** Use attachments and internal data for a company-specific request;
add Web only when current external information is needed.

**Interaction.** Occasional override.

**Visibility.** Summarized or inspector-only.

**Priority.** P0.

## 4.6 `HumanInLoopAgent`

**What it does.** Durably pauses for approve, reject, or edit. It supports
labelled review panels, rich text/JSON content, reasons, and document replacement.

**AI value.** Provides the strongest existing control boundary before material
actions and for uncertain cases.

**Chat use.** Inline approval/edit/reject card.

**Example.** Review and edit an AI-drafted email before it is sent.

**Interaction.** Direct only when genuinely required.

**Visibility.** Primary while paused.

**Priority.** P0.

## 4.7 `WebSearchAgent`

**What it does.** Runs one live query through Auto, Tavily, OpenAI, or Kimi and
returns title, URL, snippet, score, provider, and fallback reason. Results are
explicitly `candidate_only`.

**AI value.** Adds current external information.

**Chat use.** Compact search/source activity, not one message per query.

**Example.** Compare current competitor pricing against internal product plans.

**Interaction.** Automatic by default; optional Web/domain constraints.

**Visibility.** Contextual.

**Priority.** P1.

## 4.8 `IntegrationAgent`

**What it does.** Lists, searches, selects, and downloads Google Drive and
OneDrive files. Connections are referenced by ID, never embedded credentials.
Downloaded files become the same `WorkflowFileRef` contract as uploads.

**AI value.** Opens enterprise documents without introducing provider-specific
node types.

**Chat use.** Contextual connection request or file/folder picker, followed by
one Drive source activity.

**Example.** Compare a selected Drive folder with current Web research.

**Interaction.** Contextual when connection or scope is needed.

**Visibility.** Contextual.

**Priority.** P1.

## 4.9 `WorkflowFileLoader` and `KimiVisionAgent`

`WorkflowFileLoader` extracts text from PDF, DOCX, PPTX, Markdown, spreadsheets,
and code while retaining image references. `KimiVisionAgent` reads one image
from object storage and returns visual analysis without placing bytes in state.

**AI value.** Together they make uploads and downloaded files usable as
multimodal context.

**Chat use.** Attachment analysis summary and relevant findings. Parsing steps
remain hidden.

**Example.** Compare a product screenshot, requirements PDF, and financial
spreadsheet.

**Interaction.** Attach/select files.

**Visibility.** Summarized/contextual.

**Priority.** P1.

## 4.10 `MCPToolAgent`

**What it does.** Calls one author-selected MCP tool through the policy service.
It supports read/write classification, actual prior-run approval, timeout,
idempotency, retryable errors, typed results, and recovery suggestions.

**AI value.** Gives controlled access to CRM, ERP, and future enterprise systems.

**Chat use.** Compact system activity, record/table preview, connection request,
or approval card.

**Example.** Retrieve account and opportunity details, then update the CRM only
after approval.

**Interaction.** Contextual; explicit for material writes unless policy permits
unattended action.

**Visibility.** Contextual.

**Priority.** P1.

## 4.11 `SQLQueryAgent`

**What it does.** Calls a fixed MCP read-only SQL tool using parameterized SQL,
row caps, and timeouts. It has no write mode.

**AI value.** Provides exact structured facts when a typed business tool does
not already cover the question.

**Chat use.** Data table/card with query scope and truncation state.

**Example.** Find customers whose quarterly expansion exceeded 30 percent.

**Interaction.** Usually automatic within an approved data scope.

**Visibility.** Contextual result.

**Priority.** P1.

## 4.12 `DecisionAgent`

**What it does.** Applies deterministic nested IF/THEN policy and returns
decisions, matched rules, and a condition-level explanation.

**AI value.** Keeps confidence gates, missing-data checks, permissions, and
business policy explainable and stable.

**Chat use.** Hidden unless the decision changes the path or needs explanation.

**Example.** Route a low-confidence extraction to a person rather than acting.

**Interaction.** Rare.

**Visibility.** Background/inspector.

**Priority.** P1.

## 4.13 `SubprocessAgent`

**What it does.** Launches another saved workflow as an independent child run,
pauses the parent, and resumes durably after child completion. Child progress
and history remain independently observable.

**AI value.** Enables durable delegation to specialist workflows.

**Chat use.** One grouped specialist activity with expandable child details.

**Example.** Run legal, technical, and financial reviews before one consolidated
risk assessment.

**Interaction.** Rare; confirm only expensive or risky delegation.

**Visibility.** Summarized.

**Priority.** P1.

## 4.14 `EmailAgent` and `ExternalActionAgent`

`EmailAgent` searches/reads mail, creates drafts, replies, and sends through
Gmail or Microsoft Graph. `ExternalActionAgent` calls a configured REST API or
webhook with an explicit safety class. Both have side-effect protections;
email additionally distinguishes ambiguous send outcomes.

**AI value.** They turn recommendations into controlled work.

**Chat use.** Search activity, editable draft/action proposal, approval, then a
receipt or recovery card.

**Interaction.** Direct for draft/send/write approval.

**Visibility.** Contextual/primary for material action.

**Priority.** P1.

## 4.15 Artifact nodes

Generic artifact-producing nodes include:

- `PDFProposalRenderer`
- `DOCXProposalRenderer`
- `PowerPointProposalSlides`
- `OpenAIImageGenerationAgent`
- `DynamicFigureAgent`

Horizon-specific renderers add proposal formatting, citations, page limits, and
submission-readiness metadata.

**Chat use.** Artifact cards with preview, metadata, and download. The rendering
step itself remains hidden.

**Priority.** P1 generic; P3 specialized.

## 4.16 `MCPAgent`

**What it does.** Runs an LLM tool loop where the model chooses calls and
arguments. It has an optional allowlist and iteration bound.

**Risk finding.** It calls the MCP client directly, unlike `MCPToolAgent`'s
richer service-policy path. It must not be a first-class write-capable Chat tool.

**Recommended use.** Exploratory, read-only investigation with an allowlist.

**Visibility.** Advanced grouped activity.

**Priority.** P2.

---

# 5. Node opportunity matrix

**Bold entries are recommended first-class capabilities.** “Hidden” means the
capability remains inspectable but does not emit routine conversation messages.

| Existing node | AI capability | Source role | Best Chat use | Interaction | Visibility | Priority |
|---|---|---|---|---|---|---|
| **StartAgent** | Chat/form/file entry | Context intake | Composer and dynamic input | Direct | Primary | P0 |
| **TransformAgent** | AI reasoning or exact data shaping | Analysis/synthesis | Answer, draft, structured card, hidden transform | Usually indirect | Variable | P0 |
| **KnowledgeRetrieval** | Secured internal retrieval | Retrieval/filtering/provenance | Internal source activity | Optional scope | Contextual | P0 |
| **RAGAgent** | Retrieval plus grounded answer | Retrieval/ranking/citation/synthesis | Cited answer | Direct result | Primary | P0 |
| **RouterAgent** | Single/multi-route orchestration | Orchestration/filtering | Silent capability selection | Occasional override | Summarized | P0 |
| **HumanInLoopAgent** | Approve/reject/edit | Control | Inline decision card | Direct | Primary when paused | P0 |
| **EndAgent** | Result projection | Output | Final answer/artifact/handoff | Direct result | Primary | P0 |
| **WebSearchAgent** | Live Web lookup | Discovery | Compact Web source activity | Optional constraint | Contextual | P1 |
| **IntegrationAgent** | Drive/OneDrive search/download | Connection/discovery | Connect/pick files | Contextual | Contextual | P1 |
| **WorkflowFileLoader** | Parse uploaded/downloaded files | Parsing | Attachment summary | Via attachment | Summarized | P1 |
| **KimiVisionAgent** | Image understanding | Multimodal analysis | Visual findings | Attach image | Contextual | P1 |
| **MCPToolAgent** | Controlled enterprise tool | Retrieval/action | CRM/ERP activity or approval | Contextual | Contextual | P1 |
| **SQLQueryAgent** | Bounded read-only SQL | Structured retrieval | Table/data card | Usually indirect | Contextual | P1 |
| **DecisionAgent** | Deterministic policy | Filtering/authority policy | Confidence/safety gate | Rare | Hidden/inspector | P1 |
| **SubprocessAgent** | Durable delegation | Orchestration | Grouped specialist task | Rare | Summarized | P1 |
| **EmailAgent** | Search/read/draft/send email | Connection/action | Mail activity and editable draft | Direct for send | Contextual/primary | P1 |
| **ExternalActionAgent** | REST/webhook action | Connection/action | Action proposal and receipt | Approval for writes | Contextual | P1 |
| TextAssemblerAgent | Deterministic parallel join | Synthesis | One combined result | None | Hidden | P1 |
| OpenAIImageGenerationAgent | Image artifact | Output | Inline image card | Request/contextual | Primary result | P1 |
| PDFProposalRenderer | PDF artifact | Output | Document card | Direct result | Primary | P1 |
| DOCXProposalRenderer | DOCX artifact | Output | Document card | Direct result | Primary | P1 |
| PowerPointProposalSlides | PPTX artifact | Output | Slide-deck card | Direct result | Primary | P1 |
| ExcelTableExtractor | Spreadsheet extraction | Parsing | Table preview | Attach/select file | Contextual | P1 |
| PDFTextExtractor | Page-level PDF text | Parsing | Feed source adapter | None | Hidden | P2 |
| PythonSnippetAgent | Sandboxed computation | Analysis/transformation | Data/code result | Advanced | Advanced | P2 |
| MCPAgent | Autonomous tool loop | Discovery/connection | Read-only exploratory group | Occasional | Advanced | P2 |
| ScientificResearchPlannerAgent | Governed research plan | Orchestration | Plan summary | Confirm costly scope | Summarized | P2 |
| BoundedDeepResearchAgent | Bounded multi-brief research | Discovery | One research operation | Scope/budget | Summarized | P2 |
| ScholarlyCandidateDiscoveryAgent | Academic and contradiction search | Discovery | Research-source group | Optional repositories/date | Summarized | P2 |
| StructuredDatasetRetrieverAgent | Reproducible official data | Structured retrieval/provenance | Dataset evidence/table | Optional filters | Contextual | P2 |
| PriorProjectRetrieverAgent | Official EU project discovery | Discovery | Prior-project sources | Rare | Summarized | P3 |
| ResearchSourceAcquirer | Safe full-text acquisition | Acquisition/storage/provenance | Acquisition detail | None | Hidden | P2 |
| PaperQAEvidenceSynthesizerAgent | Literature synthesis | Analysis | Research synthesis | None | Summarized | P2 |
| ClaimEvidenceVerifier | Exact claim/source verification | Verification/provenance | Support/conflict badge | Investigate conflict | Contextual | P2 |
| ProposalEvidenceFactoryAgent | Full evidence qualification | Verification/ranking/citation | Evidence report | Review blockers | Specialized | P3 |
| CitationRegistryBuilder | Stable deterministic citation numbers | Citation | Generic citation adapter input | None | Hidden | P2 |
| MinIOEvidenceIngestion | Index acquired evidence | Storage/retrieval preparation | Background indexing | None | Hidden | P3 |
| InternalProjectEvidenceRetrieverAgent | Approved internal fact extraction | Retrieval/verification | Internal fact evidence | Review sensitive facts | Contextual | P2 |
| ScientificSkillAgent | Methodology-guided synthesis | Analysis | Specialist answer | Optional skill choice | Contextual | P2 |
| DynamicFigureAgent | Generate/embed several figures | Output | Grouped image artifacts | Rare | Summarized | P2 |
| FigureEmbedder | Embed stored image into content | Transformation | None | None | Hidden | P3 |
| WorkflowInputAgent | Legacy typed input | Context intake | Existing workflows only | Direct | Legacy/hidden | Legacy |
| AITaskAgent | Legacy AI task | Analysis | Existing workflows only | Indirect | Legacy/hidden | Legacy |
| DataTransformAgent | Legacy exact transform | Transformation | Existing workflows only | None | Hidden | Legacy |
| HorizonDOCXProposalRenderer | Horizon DOCX | Output | Proposal artifact | Direct result | Specialized | P3 |
| HorizonHTMLProposalRenderer | Horizon PDF/HTML | Output | Proposal artifact | Direct result | Specialized | P3 |
| GraphNormalizer | Build proposal graph | Analysis/storage | Proposal setup summary | Correct facts | Specialized | P3 |
| ConceptAlternativesAgent | Generate/judge alternatives | Analysis/comparison | Side-by-side options | Select concept | Specialized primary | P3 |
| ConceptFreezeAgent | Lock concept | Control/state | Confirmation | Direct | Contextual | P3 |
| CallCoverageMatrixAgent | Requirement coverage | Analysis/validation | Coverage table | Review gaps | Specialized | P3 |
| MethodologyEngineeringAgent | Proposal methodology | Analysis | Draft artifact | Review | Specialized | P3 |
| ConsistencyChecker | Deterministic consistency gate | Verification | Blocker summary | Fix issues | Contextual | P3 |
| HorizonEvaluationAgent | Proposal evaluation | Evaluation | Scorecard | Review | Specialized | P3 |
| ProposalTruthGraphAgent | Integrity-hashed truth graph | Storage/provenance | Inspector artifact | None | Advanced | P3 |
| ProposalSubmissionGate | Submission readiness | Validation/control | Ready/blocked card | Resolve blockers | Specialized primary | P3 |
| Literal | Test constant | None | Never expose | None | Hidden | Internal |
| Echo | Test template node | None | Never expose | None | Hidden | Internal |

---

# 6. Unified Sources

## 6.1 Product goal

The user should experience one research operation:

```text
User request
    ↓
AI identifies required information
    ↓
Allowed source branches run
    ├── Web
    ├── internal knowledge
    ├── Drive
    ├── uploads/PDFs/images
    ├── research
    └── database/enterprise systems
    ↓
Source records are normalized, ranked, and checked
    ↓
One synthesized answer with consistent citations
```

The underlying workflow may continue using specialized nodes. The UI should not
make those implementation boundaries the user's mental model.

## 6.2 Recommended architecture

```text
                         AI Chat
                            │
                  User source constraints
                            │
                 Source Planning Adapter
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
       External         Enterprise        User context
          │                 │                  │
  WebSearchAgent     KnowledgeRetrieval   Attachments
  Deep Research      IntegrationAgent     WorkflowFileLoader
  Scholarly search   MCPToolAgent         KimiVisionAgent
  Prior projects     SQLQueryAgent        Spreadsheets/PDFs
  Official datasets  EmailAgent
          │                 │                  │
          └─────────────────┼──────────────────┘
                            │
                Source Normalization Adapter
                            │
                    Unified Source Records
                            │
           Deduplication / authority / freshness
                            │
                 TransformAgent or RAGAgent
                            │
                  Answer + unified citations
```

This should be an adapter/runtime contract, not one replacement node per source.

## 6.3 Common source record

The existing contracts can normalize into a model such as:

```typescript
type UnifiedSource = {
  id: string;
  sourceType:
    | 'web'
    | 'knowledge'
    | 'drive'
    | 'upload'
    | 'image'
    | 'research'
    | 'database'
    | 'email'
    | 'enterprise';

  title: string;
  origin: string;
  locator?: string;
  preview?: string;

  documentId?: string;
  sourceId?: string;
  sourceVersionId?: string;
  chunkId?: string;

  url?: string;
  objectKey?: string;
  page?: number;
  section?: string;

  authors?: string[];
  publicationYear?: number;
  doi?: string;

  modifiedAt?: string;
  publishedAt?: string;
  accessedAt?: string;

  relevance?: number;
  authority?: string;
  evidenceStatus:
    | 'candidate'
    | 'retrieved_not_verified'
    | 'verified'
    | 'contradicted'
    | 'structured_record';

  permissionScope: string;
  metadata: Record<string, unknown>;
};
```

## 6.4 Mapping existing outputs

| Existing output | Unified mapping readiness |
|---|---|
| `KnowledgeRetrieval` citations/chunks | Strong: direct IDs, version, page, section, relevance, trace |
| `RAGAgent` citations/sources | Strong: direct citation and answer linkage |
| `IntegrationAgent` file metadata | Strong for source cards; downloaded content still needs extraction/indexing |
| `WebSearchAgent` results | Good; publication time and verification are missing |
| Scholarly candidate/full-text models | Strong: DOI/authors/year/authority/canonical/retraction metadata |
| Structured dataset records/audit | Strong: exact values, units, period, access time, hashes |
| SQL rows | Adapter required for source/query identity and row citations |
| MCP typed records | Adapter required per tool/result schema |
| Email messages | Good source identity; permission and privacy policy must govern use |
| Uploaded files | Good identity/hash; generic page/section citations need enrichment |
| Vision analysis | Adapter required; no region-level locator |

## 6.5 Source selection

### Automatic

The system can infer these defaults:

- Attachment-specific request → attachments first.
- Company-specific fact → Knowledge, Drive, MCP, SQL, or email; Web normally off.
- Current market/competitor question → internal context plus Web.
- Scholarly/state-of-art question → research pipeline.
- Exact metric → structured dataset, SQL, or typed MCP result.
- Visual question → image attachment plus vision.

A practical existing implementation is:

1. `TransformAgent` classifies source needs into a typed set of booleans/scopes.
2. `DecisionAgent` applies permission and policy rules.
3. `RouterAgent(selection: multi)` starts the permitted branches.

### User constrained

Expose a compact control:

```text
Sources: Auto
```

Expanded choices:

- Current attachments
- Internal knowledge
- Drive
- Web
- Research
- Business data
- Email

The control constrains allowed branches; it does not expose node types.

### AI asks

Ask only when:

- A connection is missing.
- A folder/file/account scope is ambiguous.
- Sensitive or unusually broad access is needed.
- Source authority conflicts cannot be resolved by policy.

## 6.6 Source scopes supported today

### Drive

Supported now: provider, connection, folder, one or many file IDs, search query,
pagination, original Web URL, and modification date.

Missing: one cross-provider search, cross-drive deduplication, and folder-to-
Knowledge-index automation.

### Uploads and documents

Supported now: current files, multiple files, category/type/size constraints,
object hash/reference, text extraction, truncation state, and image separation.

Missing: durable conversation-level attachment collections, generic locators for
all file formats, and automatic indexing into Knowledge Studio.

### Web

Supported now: provider, query, top-K, result URL/title/snippet/score, and bounded
deep research.

Missing: domain allow/deny lists in `WebSearchAgent`, generic date constraints,
publication-date normalization, and generic verification outside the proposal
evidence chain.

### Research

Supported now: repository fan-out, DOI/authors/year, contradiction search,
canonical identity, full-text acquisition, immutable source versions,
retraction status, exact-passage verification, and stable citations.

Research should remain a distinct source class rather than being flattened into
generic Web results.

### Database and enterprise systems

Supported now: typed MCP tools, read-only parameterized SQL, row/timeout caps,
official dataset retrieval, response snapshots, units, geography, period,
parameters, access time, and hashes.

Missing: one provenance envelope and generic row-level citation format.

### Images

Supported now: uploaded images, stable references, and vision analysis.

Missing: collection search, visual-region citations, and cross-modal ranking.

## 6.7 Unified citations

Use one citation syntax regardless of origin:

```text
Enterprise expansion drove revenue, while competitors moved toward
usage-based pricing. [1][2][3]
```

Then:

```text
[1] Q2 Strategy.pdf · Drive · modified 12 Aug
[2] Revenue query · Business data · accessed now
[3] Competitor pricing · Web
```

`CitationRegistryBuilder` demonstrates deterministic stable numbering, but its
contract is specialized to acquired evidence. General Chat needs an answer-
scoped registry built from `UnifiedSource` records.

## 6.8 Sources panel

Recommended contextual drawer:

```text
Sources used                                      12

All 12 | Internal 4 | Drive 2 | Web 3 | Research 2 | Data 1
```

Each card should expose:

- Title and source class.
- Relevant passage, image finding, or rows.
- Why it was used.
- Relevance and evidence status.
- Modified/published/accessed date.
- Open original.
- Associated answer citations.

Contextual actions:

- Ask about this.
- Compare with another source.
- Exclude from this answer.
- Search this source again.
- Open original.
- Inspect passage/query/rows.

## 6.9 Authority, freshness, and conflict

The current system exposes useful metadata:

- Knowledge: immutable source version and active index.
- Drive: modification time.
- Research: publication year, DOI, authority, canonical/retraction state.
- Structured datasets: access time, version, response hash.
- Web: execution/search time, but usually no publication time.
- Database: query time; business-effective dates depend on result schema.

Recommended policy defaults, configurable per workflow:

1. Exact business facts → typed database/MCP/SQL.
2. Company policy → approved internal Knowledge/Drive.
3. Current external facts → Web.
4. Scientific claims → verified primary/official evidence.
5. Uploaded material → authoritative for “what this file says,” not necessarily
   for claims about the world.
6. Images → supporting evidence when visual interpretation is relevant.

The proposal evidence chain already detects support, contradiction, and
insufficient evidence, but it is proposal-graph-specific. Generic Chat conflict
detection is a runtime/adapter gap.

Recommended conflict interaction:

```text
I found conflicting values:

• Q2 report: $18.4M
• Finance database: $18.7M

The database was queried today; the report was modified 42 days ago.

[Prefer database] [Use approved report] [Investigate difference]
```

## 6.10 Permissions

Unified sources must not imply unified access. Existing protections include:

- Session/owner/collection retrieval scope.
- Knowledge permission checks.
- OAuth connection IDs rather than tokens in workflows.
- MCP read/write/destructive policy.
- Approval derived from actual run state.
- Read-only SQL.
- Idempotency and ambiguous-failure handling.

If access is missing, Chat should say so and offer:

```text
I can answer more completely using your connected Drive, but I do not currently
have access.

[Connect Drive] [Continue without it]
```

The AI must never claim it searched a disconnected or unauthorized source.

---

# 7. High-value node combinations

## 7.1 Unified enterprise research

**Nodes:** `StartAgent` → `TransformAgent` → `RouterAgent(selection: multi)` →
parallel `KnowledgeRetrieval`, `IntegrationAgent`/`WorkflowFileLoader`,
`WebSearchAgent`, and `MCPToolAgent`/`SQLQueryAgent` → source normalization →
`TransformAgent` → `EndAgent`.

**Capability:** One answer across internal knowledge, Drive, Web, attachments,
and exact business data.

**Chat:**

```text
Used 11 sources · Internal 4 · Drive 2 · Web 3 · Account data 2
```

## 7.2 Safe enterprise action

**Nodes:** `TransformAgent` → `DecisionAgent` → `HumanInLoopAgent` →
`MCPToolAgent`, `EmailAgent`, or `ExternalActionAgent` → `EndAgent`.

**Capability:** Draft, review, and safely execute.

**Example:** Draft a renewal email from CRM and contract facts, let the user
edit it, then send once.

## 7.3 Multimodal analyst

**Nodes:** attachments → `WorkflowFileLoader` → parallel `TransformAgent`,
`KimiVisionAgent`, and `ExcelTableExtractor` → synthesis.

**Capability:** Reason across PDFs, spreadsheets, screenshots, and documents.

## 7.4 Evidence-grade research

**Nodes:** `ScientificResearchPlannerAgent` → parallel discovery nodes →
`ResearchSourceAcquirer` → `PaperQAEvidenceSynthesizerAgent` →
`ClaimEvidenceVerifier`/`ProposalEvidenceFactoryAgent` →
`CitationRegistryBuilder` → synthesis.

**Capability:** Broad discovery, immutable full text, contradiction handling,
exact-passage verification, and citations.

## 7.5 Durable specialist delegation

**Nodes:** `RouterAgent` → one or more `SubprocessAgent` children →
`TextAssemblerAgent` → `TransformAgent` → optional HITL.

**Capability:** Independent long-running specialist work with child run history
and durable parent resume.

## 7.6 Artifact-producing assistant

**Nodes:** `TransformAgent` → optional image generation → PDF/DOCX/PowerPoint
renderer → `EndAgent`.

**Capability:** Reports, presentations, proposals, and visual assets usable
outside Chat.

---

# 8. Capabilities to keep hidden

Normally hidden, but inspectable:

- Deterministic `TransformAgent` operations.
- `DecisionAgent` when policy succeeds normally.
- `TextAssemblerAgent` joins.
- `CitationRegistryBuilder`.
- `MinIOEvidenceIngestion`.
- `FigureEmbedder`.
- `ResearchSourceAcquirer` acquisition mechanics.
- `WorkflowFileLoader` extraction mechanics.
- PDF/Excel parsing operations.
- Routine router evaluations.
- Checkpoint, replay, and retry mechanics.
- Subprocess launch/delivery mechanics.
- Proposal graph state deltas.
- `Literal`, `Echo`, and deprecated nodes.

Display meaningful output, intervention, failure, or source attribution—not raw
execution existence.

---

# 9. Existing capabilities reusable in Chat

These can be unlocked largely through UI/projection work:

1. **Live execution:** authenticated SSE with bounded replay and terminal state.
2. **Durable reconstruction:** saved inputs, node outputs, timing, errors,
   model selections, artifacts, and workflow YAML.
3. **Inline HITL:** the durable gate and rich editor already exist.
4. **Pause/resume:** existing endpoints; pause occurs at the next node boundary.
5. **Retry/reuse:** failed runs can reuse completed outputs with no new model
   spend for reused nodes.
6. **Restart:** saved workflow and inputs can launch a clean new attempt.
7. **Parallel grouping:** reducer-safe fan-out already works; only presentation
   grouping is missing.
8. **Model observability:** requested/actual model, fallback, tokens, cost, and
   latency are already recorded.
9. **Artifacts:** upload, storage, extraction, preview, and download exist.
10. **Source provenance:** Knowledge, Drive, research, and dataset contracts
    already expose substantial metadata.
11. **Friendly experience copy:** `NodeExperienceSpec` has running, completed,
    failure, and recovery copy.
12. **Structured output:** node schemas can drive tables, forms, cards, and
    editable fields.

---

# 10. Composer and automatic-selection policy

## Permanent controls

- Attach.
- Sources menu.
- Send.
- Dictation when browser-supported.

## Optional menu

- Model.
- Desired artifact/output type.
- Source scope.
- Saved context/environment for advanced users.

## Automatic

- Web use.
- Internal Knowledge use.
- Drive search.
- Research repositories.
- Vision.
- Business-data tools.
- Specialist subprocesses.
- Model selection unless policy fixes it.

## Contextual request

- Connect Drive, email, or enterprise system.
- Choose among ambiguous accounts/files.
- Confirm sensitive or broad source scope.
- Approve send/write/external action.
- Resolve source conflict.
- Supply missing required information.

## Never expose as composer concepts

- Router mode.
- Retrieval-profile internals.
- MCP architecture/server details.
- SQL.
- Reranking/compression.
- Citation registry.
- Checkpoints and joins.

---

# 11. Gap analysis

## 11.1 UI gaps

- No complete unified Sources panel.
- No compact source-type/scope filter shared by all workflows.
- No consistent citation card across Web, Drive, database, research, and uploads.
- No durable “Ask this source” conversation state.
- Incomplete projection for many node-specific outputs.
- Source parallelism is not yet aggregated semantically.
- Pause/retry/restart are not all first-class Chat actions.
- Subprocess and pipeline activity is not one conversational activity tree.

## 11.2 Integration gaps

- Heterogeneous source outputs need common adapters.
- Drive downloads do not automatically become indexed Knowledge sources.
- Chat attachments do not automatically enter persistent retrieval context.
- SQL and MCP rows need generic provenance/citation adapters.
- Email results need source normalization.
- Vision findings need source locators.
- Proposal evidence outputs need a generic research adapter.

## 11.3 Runtime gaps

- No generic durable `UnifiedSource`/`SourceResult` collection.
- No source-planner contract independent of hard-coded workflow graphs.
- No generic source conflict detector.
- No generic “continue without failed source” transition.
- No generalized input request beyond the HITL approve/edit/reject contract.
- No retry-from-message or branch-conversation state.
- No first-class persistent assistant memory policy. Run state and post-run chat
  exist, but they are not reusable long-term assistant memory.
- Token events exist, but complete provider-neutral incremental message streaming
  is not yet a stable Chat event contract.

## 11.4 Capability gaps

- Natural-language source planning across arbitrary connected sources.
- Generic source authority/freshness policy.
- Cross-source deduplication outside the scholarly evidence pipeline.
- Generic conflict detection for exact claims/facts.
- Image collection retrieval and region-level citations.
- Generic table/row citations.
- Generic Web publication-date extraction.
- Saved mixed-source collections spanning providers.

## 11.5 Security gap

`GET /api/files` currently accepts known `workflows/` or `evidence/` object keys
and contains a code comment that session-scoped access is future work. Before a
unified Sources panel broadly exposes “open original” links, artifact retrieval
must enforce run/session ownership.

## 11.6 Agency risk

`MCPAgent` should remain read-only and advanced. It directly calls the MCP client
for model-selected tools and does not use the stronger policy/approval/
idempotency path implemented by `MCPToolAgent`.

---

# 12. Priority roadmap

## P0 — Chat foundation

- Native projections for `StartAgent`, `TransformAgent`, `HumanInLoopAgent`,
  `RAGAgent`, `KnowledgeRetrieval`, `RouterAgent`, and `EndAgent`.
- Durable conversation reconstruction.
- Workflow state strip and inspector.
- Unified attachments.
- Pause/resume/retry/restart controls.
- Artifact authorization fix.

## P1 — AI amplification

- Common source model and Sources panel.
- Adapters for Web, Knowledge, Drive, uploads, MCP, SQL, email, and images.
- AI-assisted source planning through typed `TransformAgent` output,
  `DecisionAgent` policy, and multi-route orchestration.
- Source filters/scopes and consistent citations.
- Connection requests and safe action flows.
- Generic artifact cards.

## P2 — Advanced agency

- Grouped deep research.
- Generic adaptation of scholarly acquisition/verification.
- Subprocess activity trees.
- Source conflict and authority/freshness policy.
- Ask-this-source and persistent source context.
- Read-only hardening or continued restriction of `MCPAgent`.

## P3 — Specialized experiences

- Proposal Engineering assistant.
- Scientific skill workflows.
- Official structured-data research.
- Sandboxed Python developer/data workflows.
- Horizon evidence/evaluation/rendering.
- Multi-workflow pipelines and staged review.

---

# 13. Recommended first validation experience

## Unified customer/account intelligence and response

### User request

> Review the attached customer message, check our internal documents and account
> records, use current external information only if relevant, then draft the
> best response. Ask me before sending anything.

### Existing nodes

1. `StartAgent`
2. `WorkflowFileLoader`
3. `TransformAgent`
4. `RouterAgent(selection: multi)`
5. Parallel:
   - `KnowledgeRetrieval`
   - `MCPToolAgent`
   - `SQLQueryAgent`
   - `WebSearchAgent` only when relevant
6. Source normalization adapter
7. `TransformAgent` for synthesis/draft
8. `DecisionAgent` for policy/confidence
9. `HumanInLoopAgent`
10. `EmailAgent`
11. `EndAgent`

### Conversation

1. User attaches a message or document.
2. AI extracts identity, intent, missing information, and source needs.
3. Internal knowledge and account data run automatically.
4. Web runs only if external/current facts are needed.
5. Chat reports one source activity:

   ```text
   Used 9 sources · Internal 4 · Account data 3 · Attachment 1 · Web 1
   ```

6. AI presents one analysis and editable response draft.
7. User approves or edits.
8. Email sends through existing approval-aware idempotent infrastructure.
9. Chat confirms the action and offers source/action inspection.

### Why this is the best validation

It combines natural-language understanding, attachments, unified sources,
parallel work, intelligent source selection, enterprise data, current Web
information, structured output, minimum intervention, editing, approval, real
action, recovery, audit, and a meaningful final result. It validates the
general architecture better than a text-only research demo or a specialized
proposal workflow.

---

# 14. Direct answers

## Which nodes should become first-class Chat capabilities?

- Conversation/attachments: `StartAgent`.
- AI reasoning/structured output: `TransformAgent`.
- Internal grounded knowledge: `KnowledgeRetrieval`, `RAGAgent`.
- Current external information: `WebSearchAgent`.
- Connected files: `IntegrationAgent`, `WorkflowFileLoader`.
- Image understanding: `KimiVisionAgent`.
- Enterprise data/tools: `MCPToolAgent`, `SQLQueryAgent`.
- Automatic orchestration: `RouterAgent`, `DecisionAgent`.
- User control: `HumanInLoopAgent`.
- Delegation: `SubprocessAgent`.
- Controlled action: `EmailAgent`, `ExternalActionAgent`.
- Artifacts: generic renderers and image generation nodes.
- Final projection: `EndAgent`.

## Which should remain behind the scenes?

Deterministic transformations, joins, parsing, citation construction, evidence
indexing, routine rules/routes, checkpoint mechanics, child-run plumbing,
proposal-graph state changes, deprecated nodes, `Literal`, and `Echo`.

## Which combinations amplify AI most?

1. Knowledge + Drive + Web + enterprise data + one synthesis step.
2. AI draft + HITL + email/MCP/external action.
3. Attachments + parsing + vision + spreadsheet extraction.
4. Research planning + discovery + acquisition + exact verification + citations.
5. Router + parallel branches + subprocesses + deterministic join.
6. AI synthesis + artifact renderers.

## How can the existing system provide one unified source experience?

Retain specialized source nodes underneath. Add a source-planning adapter that
selects permitted branches, a normalization contract that maps every result to
`UnifiedSource`, an answer-scoped citation registry, and one Sources panel.
Use existing `TransformAgent` + `DecisionAgent` + multi-route orchestration for
automatic selection. Reuse existing provenance, OAuth, session scoping, MCP
policy, and evidence verification unchanged where possible.

UI-only or low-backend work:

- Source chips/picker.
- Grouped source activity.
- Source cards and filters.
- Existing provenance display.
- Ask-this-source UI once conversation scope is represented.

Adapters required:

- Drive, Web, upload, image, email, SQL, MCP, dataset, and research outputs to
  one source model.
- Generic citations for rows and tool records.

Runtime work required:

- Durable unified-source records.
- Generic source planning/conflict contracts.
- Persistent source context.
- Artifact authorization.

Genuinely missing capabilities:

- Cross-source deduplication and authority/freshness policy.
- Generic conflict detection.
- Region-level image citations.
- Generic table/row citations.
- Mixed-provider saved source collections.

The conclusion is straightforward: the Builder already has the underlying
agency. The product transformation comes from projecting those capabilities as
one conversation, one source system, and a small number of meaningful user
decisions—not from exposing the graph or adding more node types.