# Workflow Node Audit and Consolidation Plan

Audit date: 2026-08-23. This inventory is derived from `NodeRegistry`, the
Pydantic schemas and `run()` implementations under `app/nodes`, the Builder
manifest/editors, and YAML found in `workflows/`, `workflows/reference/`, and
`workflows/.builder/drafts/`.

## Conclusions

- There are **57 registered node types**. All use one `NodeType` contract, one
  LangGraph compiler, one executor, one template/reference mechanism, and one
  schema/preflight system. There are not 57 workflow engines.
- Chat is an entry/output experience over workflows: `StartAgent` has a
  `chatbot` mode and `EndAgent` has a `chat_response` mode. It therefore reuses
  workflow AI, RAG, search, files, tools, and model routing rather than needing
  Chat-specific node implementations.
- `SubprocessAgent` supplies workflow composition inside a graph. No second
  node SDK is justified.
- Run controls and navigation over completed or paused runs are not executable
  workflow node types.
- No registered Schedule/Webhook Trigger, Wait/Delay, For Each/Loop, or generic
  Merge node exists. Current shipped workflows do not justify adding these.
  Parallel fan-out is represented by edges; multiple upstream references are
  accepted directly; `TextAssemblerAgent` is the meaningful deterministic text
  join.
- Node streaming metadata exists but no registered node currently streams.
  Node/run lifecycle events are streamed uniformly by the runtime event bus.
- LLM retries are shared by the gateway. Run retries reuse successful node
  checkpoints. Email and MCP writes use the shared external-operation ledger so
  ambiguous side effects are not silently replayed.

## Usage notation

`S/R/D` means node instances in shipped root workflows / reference workflow
corpus / Builder drafts. Reference counts include repeated generated fixtures,
so they measure regression exposure rather than unique business processes.

## Complete inventory and decision map

| Current Node | Current purpose | Usage S/R/D | Decision | Proposed node / surface | Migration | Reason |
|---|---|---:|---|---|---|---|
| AITaskAgent | Legacy configurable AI task | 0/8/3 | Deprecate | AI (`TransformAgent`, AI mode) | Keep type registered; already hidden; migrate only on explicit save/conversion | Shared AI primitive now has its language, retry and structured-output capabilities |
| BoundedDeepResearchAgent | Multi-query bounded research dossiers | 3/168/1 | Keep | Advanced: Bounded research | None | Stateful bounded research orchestration is more than one prompt |
| CallCoverageMatrixAgent | Deterministic call-requirement coverage matrix | 2/105/0 | Keep | Advanced: Call coverage | None | Auditable domain algorithm, not generic generation |
| CitationRegistryBuilder | Deterministic normalized citation registry | 1/63/0 | Keep | Advanced: Build citation registry | None | Provenance contract is useful and non-AI |
| ClaimEvidenceVerifier | Verify claims against evidence | 0/7/0 | Keep | Advanced: Verify evidence | None | Specialized verification outputs and provenance |
| ConceptAlternativesAgent | Generate and judge proposal concepts | 2/105/0 | Keep | Advanced: Concept alternatives | None | Multi-candidate/judging contract exceeds a basic AI preset |
| ConceptFreezeAgent | Resolve approved concept selection | 2/105/0 | Keep | Advanced: Freeze concept | None | Deterministic bridge from human gate to proposal state |
| ConsistencyChecker | Cross-document consistency checking | 4/182/1 | Keep | Advanced: Consistency check | None | Stable domain schema and high existing use |
| DOCXProposalRenderer | Render proposal DOCX | 0/7/0 | Keep | Output preset: DOCX | None | Produces an actual artifact; not an AI transform |
| DataTransformAgent | Legacy deterministic mappings | 34/119/26 | Deprecate | Transform (`TransformAgent`, deterministic mode) | Keep registered/hidden; preserve YAML; optional explicit conversion | Operations already share implementation with TransformAgent |
| DecisionAgent | Deterministic IF/THEN facts | 29/147/17 | Keep | Flow: Condition / Decision | None | Auditable rules differ from graph routing |
| DynamicFigureAgent | Generate figures from document markers | 1/63/1 | Keep | Advanced: Generate marked figures | None | Marker scanning and artifact lifecycle are specialized |
| Echo | Return text unchanged | 1/64/2 | Keep | Advanced: Echo | None | Tiny deterministic diagnostic primitive; deletion saves little and breaks fixtures |
| EmailAgent | Search/read/draft/reply/send email | 5/14/1 | Edit | Tool: Email | Preserve type/config; task-oriented label only | Uses shared email integration and operation ledger; specialized UI remains useful |
| EndAgent | Define workflow/chat result | 48/468/19 | Keep | Finish: Output | None | Generic output primitive with workflow, custom, and Chat modes |
| ExcelTableExtractor | Extract spreadsheet tables | 0/7/0 | Edit | Tool/Input: Read spreadsheet | Preserve type/config | Real parser behavior; surface by task rather than file-format node family |
| ExternalActionAgent | Call REST API or webhook | 0/7/0 | Edit | Tool: API action | Preserve type/config and side-effect marking | Generic external tool contract; confirmation semantics remain explicit |
| FigureEmbedder | Insert generated figures into content | 2/28/1 | Keep | Advanced: Embed figures | None | Deterministic artifact composition |
| GraphNormalizer | Normalize proposal/evidence graph | 2/105/1 | Keep | Advanced: Normalize evidence graph | None | Domain graph invariants are meaningful behavior |
| HorizonDOCXProposalRenderer | Render Horizon DOCX with compliance features | 8/322/3 | Keep | Advanced Output: Horizon DOCX | None | High use and substantial renderer/compliance behavior |
| HorizonEvaluationAgent | Evaluate against Horizon criteria | 7/259/2 | Keep | Advanced: Horizon evaluation | None | Domain rubric and typed scoring contract |
| HorizonHTMLProposalRenderer | Render Horizon HTML | 3/105/0 | Keep | Advanced Output: Horizon HTML | None | Actual artifact renderer |
| HumanInLoopAgent | Pause for approve/reject/edit | 40/694/8 | Edit | Flow: Human Review | Preserve type; business-language label | Genuine pause/resume runtime primitive; presets cover interaction variants |
| IntegrationAgent | Configurable HTTP integration | 0/7/0 | Edit | Tool: Integration | Preserve type/config | General tool capability with specialized endpoint/auth UI |
| InternalProjectEvidenceRetrieverAgent | Retrieve internal project evidence | 1/28/0 | Keep | Retrieve preset: Internal projects | None | Deterministic provenance-aware retrieval |
| KimiVisionAgent | Vision analysis | 2/35/1 | Keep | Advanced AI: Analyze image | None | Multimodal provider/file handling differs from text AI path |
| KnowledgeRetrieval | Retrieve knowledge chunks | 4/25/3 | Edit | Tool: Search knowledge | Preserve type/config | Common deterministic retrieval; should be task-visible without folding into AI |
| Literal | Emit configured values | 6/105/1 | Edit | Data: Input / Constant | Preserve type/config | Useful deterministic source, but surface as an Input preset |
| MCPAgent | Execute legacy MCP skill contract | 1/7/0 | Keep | Advanced Tool: MCP skill | None | Existing protocol behavior; MCPToolAgent is preferable for new individual tools |
| MCPToolAgent | Execute a selected MCP tool | 63/518/38 | Keep | Get / Do: Tool | None | Shared Chat/workflow tool infrastructure and highest tool usage |
| MethodologyEngineeringAgent | Engineer proposal methodology | 2/105/0 | Keep | Advanced: Methodology engineering | None | Typed domain workflow capability |
| MinIOEvidenceIngestion | Ingest evidence from object storage | 1/63/0 | Keep | Advanced Tool: Ingest evidence | None | Storage/provenance side effects are not a prompt preset |
| OpenAIImageGenerationAgent | Generate images | 11/56/5 | Keep | Tool/Create: Generate image | None | Produces binary artifact through provider API |
| PDFProposalRenderer | Render proposal PDF | 0/7/0 | Keep | Output preset: PDF | None | Actual artifact renderer |
| PDFTextExtractor | Extract PDF text | 0/7/0 | Edit | Tool/Input: Read PDF | Preserve type/config | Real parser; reuse file pipeline but retain parser handler |
| PaperQAEvidenceSynthesizerAgent | Synthesize paper QA evidence | 1/28/0 | Keep | Advanced: Paper evidence synthesis | None | Evidence-linked typed outputs exceed generic AI |
| PowerPointProposalSlides | Render proposal slides | 1/77/0 | Keep | Output preset: Presentation | None | Actual presentation renderer |
| PriorProjectRetrieverAgent | Retrieve prior-project evidence | 1/28/0 | Keep | Retrieve preset: Prior projects | None | Specialized source and provenance contract |
| ProposalEvidenceFactoryAgent | Build proposal evidence package | 2/105/0 | Keep | Advanced: Evidence factory | None | Multi-artifact domain contract |
| ProposalSubmissionGate | Deterministic submission readiness gate | 5/196/1 | Keep | Advanced Condition: Submission gate | None | Compliance logic must remain deterministic and inspectable |
| ProposalTruthGraphAgent | Build proposal truth graph | 2/105/0 | Keep | Advanced: Truth graph | None | Specialized graph/provenance behavior |
| PythonSnippetAgent | Execute Python snippet | 4/7/3 | Keep | Advanced: Code | None | Genuine code execution; clearer than pretending it is a transform |
| RAGAgent | Retrieve knowledge and generate grounded answer | 12/469/5 | Keep | Tool + AI: Ask knowledge | None | Shared retrieval/model services, citations and grounding are meaningful compound behavior |
| ResearchSourceAcquirer | Acquire full research sources | 2/105/0 | Keep | Advanced Tool: Acquire sources | None | Network/file acquisition and provenance behavior |
| RouterAgent | Select one or multiple graph branches | 37/317/14 | Keep | Flow: Router | None | Compiler-level conditional dispatch; cannot be a plain condition output |
| SQLQueryAgent | Execute SQL through configured MCP system | 0/7/0 | Edit | Tool: Database query | Preserve type and specialized query UI | Tool contract is shared; SQL UI remains valuable |
| ScholarlyCandidateDiscoveryAgent | Discover scholarly candidates | 2/105/0 | Keep | Tool preset: Search scholarly sources | None | Multi-query/search-audit contract |
| ScientificResearchPlannerAgent | Plan scientific research | 2/105/1 | Keep | Advanced: Research plan | None | Domain plan schema and constraints |
| ScientificSkillAgent | Execute installed scientific skill | 1/14/0 | Keep | Advanced Tool: Scientific skill | None | Existing skill runtime and tool dependency |
| StartAgent | Collect form or chat inputs | 11/151/5 | Edit | Data: Input | Keep type; display as Input; presets choose Form or Chat | Canonical unified Input primitive already supports files and Chat |
| StructuredDatasetRetrieverAgent | Retrieve structured datasets | 1/28/0 | Keep | Retrieve preset: Dataset | None | Dataset-specific provenance and shape |
| SubprocessAgent | Run a saved workflow and wait | 12/21/9 | Edit | Advanced Flow: Subworkflow | Preserve type/config; rename surface | Requested composition primitive already exists with durable child runs |
| TextAssemblerAgent | Wait for and combine text branches | 13/273/6 | Edit | Data: Combine text | Preserve type/config; label as Combine | Meaningful deterministic merge with completeness signal |
| TransformAgent | AI transformation or deterministic operations | 80/1846/40 | Keep | Create: AI / Data: Transform | None | Shared primitive; now supplies Summarize, Analyze, Extract, Classify, Draft, Rewrite, Critique, Translate, Compare and FAQ presets |
| WebSearchAgent | Search the public web | 2/84/1 | Edit | Tool preset: Search web | Preserve type/config | Shared external search capability with result/audit semantics |
| WorkflowFileLoader | Load and parse workflow file references | 6/161/1 | Edit | Input preset: Read document | Preserve type/config | Reuses common object-storage refs and performs real parsing |
| WorkflowInputAgent | Legacy typed workflow input projection | 3/35/2 | Deprecate | Input (`StartAgent`) | Keep registered/hidden; preserve saved YAML | StartAgent supersedes it and unifies form/chat/file entry |

## New primitive evaluation

| Candidate | Decision | Evidence |
|---|---|---|
| Human Review | Existing | `HumanInLoopAgent` already pauses durably and supports approve/reject/edit |
| Condition | Existing | `DecisionAgent` handles deterministic facts; `RouterAgent` handles graph branches |
| Subworkflow | Existing | `SubprocessAgent` launches an independently auditable child workflow |
| Transform | Existing | `TransformAgent` has AI and 14 deterministic operation modes |
| Code | Existing | `PythonSnippetAgent`; SQL remains a Tool |
| Structured Output | Configuration | AI/Transform output fields and End mappings already provide it |
| Merge | No new node | Nodes accept multiple upstream mappings; `TextAssemblerAgent` handles meaningful text combine |
| For Each | Defer | No shipped usage demonstrates user-controlled collection iteration; research nodes already bound their own collection work |
| Wait/Delay | Defer | No long-running schedule/automation trigger system is registered |
| Trigger | Defer/workflow-level | No registered automated-start capability; add as workflow metadata if automation enters scope |

## Compatibility and execution contract

- Registry keys, YAML, schemas, and runtime handlers remain unchanged.
- Deprecated types remain registered and configurable but are hidden from new
  node creation. Migration is opt-in because current workflows are valid.
- Common values already travel as Pydantic-inferred text, number, boolean,
  object, list/table-like objects, and `WorkflowFileRef`; references use the
  shared `{{outputs.step.field}}` / `{{inputs.field}}` syntax.
- Sequential edges establish execution order. Authors map data only where a
  node needs a specific value; Start/End and pipeline stages already provide
  convention-based boundary mapping.

## Complexity measurement

### Before

- Registered node types: **57**
- Backend `NodeType.run` handlers: **57**
- Canvas node renderers: **2** (`WorkflowNode`, plus non-executable `NoteNode`)
- Purpose-built Builder config editors: **14**, with schema form fallback
- Workflow execution engines: **1**
- Registry-classified specialized node types: **43**
- Compatibility types hidden from creation: **3**

### After this safe consolidation

- Registered node types: **57** (zero saved-workflow breakage)
- Backend `NodeType.run` handlers: **57** (working domain guarantees retained)
- Canvas node renderers: **2**
- Purpose-built Builder config editors: **14**
- Workflow execution engines: **1**
- Registry-classified specialized node types: **43** (retained behind task search/advanced categories)
- User-facing core concepts: **Input, AI, Tool, Transform, Condition/Router,
  Human Review, Subworkflow, Code, Output**
- Compatibility types hidden from creation: **3**
- New first-class runtime types: **0**
- AI task presets backed by one runtime type: **10**

The reduction is intentionally in concepts and creation paths rather than an
unsafe immediate deletion of handlers. Existing specialized implementations,
Chat services, MCP tools, model gateway, file ingestion, retries, and artifact
renderers are reused unchanged.