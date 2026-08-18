# Node Coverage Matrix

Computed directly from the built workflow YAML (`grep '^\s*type:' workflows/{sp,w}*.yaml`), not from memory or intent. W01–W10 are the ten business workflows; SP01–SP05 are the general-purpose reusable subprocesses; W01sub/W08sub are workflow-specific subprocesses (not intended for reuse outside their parent).

Legend: **✓** = used as a real node in that file. Blank = not used there.

## Core Building Blocks (12 registered; 9 authorable — 3 hidden from the palette)

| Node | W01 | W02 | W03 | W04 | W05 | W06 | W07 | W08 | W09 | W10 | Subprocesses |
|---|---|---|---|---|---|---|---|---|---|---|---|
| StartAgent | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — (subprocesses have no Start) |
| EndAgent | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | all 8 subprocess files |
| DecisionAgent | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | | SP02, SP03, W01sub(no), W08sub_it_account |
| RouterAgent | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | SP05, W01sub |
| HumanInLoopAgent | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | SP04, SP05 |
| EmailAgent | ✓* | | | ✓ | | | | ✓ | | | SP05 |
| MCPToolAgent | ✓* | | ✓ | ✓ | ✓ | | ✓ | | | | SP02, W01sub |
| IntegrationAgent | | | | | | | | | | | not in the 10 — see note below |
| ExternalActionAgent | | | | | | | | | | | not in the 10 — see note below |
| ~~WorkflowInputAgent~~ | | | | | | | | | | | hidden from palette, correctly excluded |
| ~~AITaskAgent~~ | | | | | | | | | | | hidden from palette, correctly excluded |
| ~~DataTransformAgent~~ | | | | | | | | | | | hidden from palette, correctly excluded |

\* W01 itself doesn't call `EmailAgent`/`MCPToolAgent` directly — they're used inside its subprocess (`w01sub_route_and_notify.yaml`, called from W01), which is exactly the reuse pattern this portfolio is meant to demonstrate.

## Control & Flow (6 registered; all 6 authorable)

| Node | W01 | W02 | W03 | W04 | W05 | W06 | W07 | W08 | W09 | W10 | Subprocesses |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TransformAgent | ✓ | ✓ | ✓ | | | | ✓ | ✓ | ✓ | ✓ | SP01, SP05, W08sub_hr_setup, W08sub_it_account |
| TextAssemblerAgent (Join) | | | | ✓ | | | ✓ | ✓ | | ✓ | |
| SubprocessAgent | ✓ | | ✓ | ✓ | | | | ✓ | ✓ | | W01sub (calls SP05) |
| WorkflowFileLoader | | | | | | | ✓ | | | ✓ | |
| Echo | | | | | | | | | | | SP03 |
| Literal | | | | | | | | | | | not used — see note below |

## Research & Discovery (5 registered — 5/5 covered)

| Node | Used in |
|---|---|
| ScientificResearchPlannerAgent | W10 |
| BoundedDeepResearchAgent | W10 |
| ScholarlyCandidateDiscoveryAgent | W10 |
| ResearchSourceAcquirer | W10 |
| WebSearchAgent | W02, W10 |

## Evidence & Retrieval (10 registered — 3/10 covered)

| Node | Used in |
|---|---|
| RAGAgent | W02, W03, W06, SP03 |
| KnowledgeRetrieval | W02, W06, SP03 |
| ProposalTruthGraphAgent | W10 |
| InternalProjectEvidenceRetrieverAgent | not used — see note |
| PriorProjectRetrieverAgent | not used — see note |
| StructuredDatasetRetrieverAgent | not used — see note |
| PaperQAEvidenceSynthesizerAgent | not used — see note |
| MinIOEvidenceIngestion | not used — see note |
| ClaimEvidenceVerifier | not used — see note |
| CitationRegistryBuilder | not used — see note |

## Proposal Engineering (9 registered — 9/9 covered)

| Node | Used in |
|---|---|
| GraphNormalizer | W10 |
| ConceptAlternativesAgent | W10 |
| ConceptFreezeAgent | W10 |
| MethodologyEngineeringAgent | W10 |
| ProposalEvidenceFactoryAgent | W10 |
| ConsistencyChecker | W10 |
| CallCoverageMatrixAgent | W10 |
| HorizonEvaluationAgent | W10 |
| ProposalSubmissionGate | W10 |

## Multimodal (4 registered — 0/4 covered)

None of the 10 business processes have a genuine image/vision requirement — see the exclusion note below rather than a forced fit.

## Document Rendering & Export (7 registered — 3/7 covered)

| Node | Used in |
|---|---|
| PowerPointProposalSlides | W10 |
| HorizonDOCXProposalRenderer | W10 |
| HorizonHTMLProposalRenderer | W10 |
| ExcelTableExtractor | not used — see note |
| PDFTextExtractor | not used — see note |
| PDFProposalRenderer | not used — superseded by the Horizon-specific renderers used in W10 |
| DOCXProposalRenderer | not used — superseded by the Horizon-specific renderer used in W10 |

## Integrations (4 registered — 2/4 covered)

| Node | Used in |
|---|---|
| MCPAgent | W09 |
| PythonSnippetAgent | SP02, W07 |
| ScientificSkillAgent | not used — see note |
| SQLQueryAgent | not used — see note |

---

## Coverage arithmetic

```
Registered node types (app/nodes/registry.py):                    57
UI-authorable / demo-eligible (excludes 3 hidden-from-palette):    54
Distinct node types used across the 10 workflows + subprocesses:  34
Coverage within this portfolio:                            34 / 54 = 63.0%

Also demonstrated this session, outside the 10-workflow portfolio:
  IntegrationAgent — workflows/test_fixtures/google_drive_rag_lookup.yaml
Coverage including that:                                    35 / 54 = 64.8%
```

## Exclusions — every uncovered demo-eligible node, with a reason

Per the brief's own §19 and §30: coverage is measured across the *portfolio*, not per workflow, and a node is only used where it has a real business reason — technical-feature-stacking without one is explicitly what this brief warns against. Every exclusion below is a deliberate decision, not an oversight.

```
Node:      IntegrationAgent
Reason:    Not needed by any of the 10 target business processes as scoped —
           none of them centers on cloud-file browsing/selection. Already
           demonstrated, preflight-verified, elsewhere in this session.
Evidence:  workflows/test_fixtures/google_drive_rag_lookup.yaml (PASS, 5 nodes).

Node:      ExternalActionAgent
Reason:    Every external system these 10 processes touch (CRM, ERP, MySQL
           business records) is already MCP-connected — there was no
           external, non-MCP REST/webhook target that any of the 10
           processes genuinely needed to call.
Evidence:  app/mcp/{dynamics,d365_finance,business_records}/ — all three
           connected systems are MCP servers, confirmed via their real
           tool catalogs (see INTEGRATION_COVERAGE.md).

Node:      Literal
Reason:    Its own class docstring frames it as smoke-test scaffolding
           ("useful for smoke tests"). No real business workflow needs a
           hardcoded constant-value node — every value in these 10
           workflows comes from a real form field, extraction, or lookup.
Evidence:  app/nodes/_stubs.py.

Node:      InternalProjectEvidenceRetrieverAgent
Reason:    W10's evidence pipeline is grounded entirely in public research
           (BoundedDeepResearchAgent/ScholarlyCandidateDiscoveryAgent) —
           there was no internal partner/pilot dataset in scope for this
           demo's concept note to retrieve facts from.

Node:      PriorProjectRetrieverAgent
Reason:    Same pipeline shape as above — no CORDIS/LIFE/EIP-AGRI prior-art
           search was in scope for the demo concept note used.

Node:      StructuredDatasetRetrieverAgent
Reason:    W10's concept note has no bounded Eurostat/structured-data claim
           that needed a reproducible statistical retrieval.

Node:      PaperQAEvidenceSynthesizerAgent
Reason:    W10 already runs ProposalEvidenceFactoryAgent, which performs the
           same acquired-documents claim verification this node would
           duplicate for the same document set — using both would be
           redundant technical-feature-stacking, not real business value
           (the exact anti-pattern this brief's §30 warns against).

Node:      ClaimEvidenceVerifier
Reason:    Same reasoning as PaperQAEvidenceSynthesizerAgent —
           ProposalEvidenceFactoryAgent's own verification step already
           does this job for W10's chosen pipeline shape. Real
           alternative, not a gap.

Node:      CitationRegistryBuilder
Reason:    ProposalEvidenceFactoryAgent already produces its own
           `citation_registry` internally (confirmed directly against
           preflight's own field list for that node) — adding a second,
           separate citation-numbering step over the same documents would
           renumber sources that are already numbered.

Node:      MinIOEvidenceIngestion
Reason:    W10 doesn't index acquired sources for later RAG retrieval by a
           section-drafting RAGAgent — the two drafting steps
           (draft_excellence/draft_impact) are grounded in the frozen
           methodology text directly, not a fresh retrieval query. This
           node's job (stamping citation numbers onto indexed chunks) has
           no consumer in this specific pipeline shape.

Node:      KimiVisionAgent / OpenAIImageGenerationAgent / DynamicFigureAgent / FigureEmbedder
Reason:    None of the 10 target business processes has a genuine
           image-analysis or image-generation requirement. Forcing an
           image-generation step into an invoice-verification or purchase-
           approval workflow would be exactly the "showcase for its own
           sake" this brief's §30 explicitly prohibits.

Node:      ExcelTableExtractor / PDFTextExtractor
Reason:    Both W07 and W10 already use WorkflowFileLoader, which
           generically extracts text from the same file types (PDF, DOCX,
           spreadsheets) these two nodes handle individually. Using the
           narrower, format-specific extractor alongside the generic one
           on the same file would be redundant.

Node:      PDFProposalRenderer / DOCXProposalRenderer
Reason:    W10 uses the Horizon-specific renderers instead
           (HorizonDOCXProposalRenderer / HorizonHTMLProposalRenderer),
           which are the domain-appropriate superset (citation numbering,
           TOC, page-limit enforcement) for exactly this proposal's shape —
           per this platform's own guidance, prefer the Horizon-specific
           renderer once citation numbering or a page limit is needed.

Node:      ScientificSkillAgent
Reason:    W10's drafting steps are grounded directly in the frozen,
           verified methodology (draft_excellence/draft_impact read
           `methodology.method_cards`) rather than needing curated
           Scientific Agent Skill guidance layered on top for this demo's
           scope.

Node:      SQLQueryAgent
Reason:    Every specific lookup these 10 processes needed already has a
           classified, purpose-built MCP tool (find_customer,
           find_sales_order, create_case, etc.) — SQLQueryAgent exists
           specifically for when no classified tool covers a lookup, which
           never arose here. W09's one open-ended, tool-unknown-upfront
           case used MCPAgent (with `allowed_tools: [query_readonly]`)
           instead, which is the platform's own documented distinction
           between "the exact operation is known" (MCPToolAgent/
           SQLQueryAgent) and "the goal is known, the operation isn't"
           (MCPAgent).
```
