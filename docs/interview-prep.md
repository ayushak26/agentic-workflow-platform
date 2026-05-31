# Interview Prep — Prosus Finance AI, Data & AI Engineer

## How to Use This Doc

Each question has:
- The likely question phrasing
- The 30-second answer
- The resume bullet it maps to
- The phase/file in this build that proves it

Say the answer out loud. If you can't, shorten it.

---

## RAG and Retrieval

**Q1. What is naive RAG and why isn't it enough?**
Naive RAG = embed query → nearest neighbor search → stuff top-k into context.
It fails when documents are long (chunking matters), when the query is ambiguous
(you need query understanding), when metadata filters matter (hybrid search beats pure vector),
and when the top-k results are not the most relevant (reranking matters).
Resume bullet: "Hybrid Weaviate RAG with re-ranking"
Proof: app/retrieval/ — query_understanding.py, hybrid_search.py, reranker.py, compressor.py

**Q2. What is hybrid search and when does it beat pure vector search?**
Hybrid search combines BM25 (keyword) and dense vector search, then fuses scores.
BM25 wins when the query contains rare keywords (company names, contract numbers).
Vector wins when the query is semantic (find me documents about project risk).
Hybrid wins for production because real user queries are a mix.
Proof: app/retrieval/hybrid_search.py, Weaviate hybrid() query with alpha parameter

**Q3. What is contextual compression and why do you do it after retrieval?**
After reranking you have 10 chunks. Most of them contain irrelevant sentences alongside
the relevant ones. Contextual compression strips the irrelevant sentences so the
LLM context window only contains signal.
Proof: app/retrieval/compressor.py

**Q4. How do you prevent hallucination in RAG outputs?**
Three mechanisms: (1) prompt instructs the model to cite chunk IDs, (2) the MCP server's
`validate_citation` tool checks that the cited chunk ID actually exists in Weaviate,
(3) the LLM-as-a-Judge scores citation accuracy on the golden set.
Proof: app/mcp/server.py (validate_citation), app/evaluation/judge.py (citation_accuracy criterion)

---

## Agents and LangGraph

**Q5. What is LangGraph and why use it over plain LangChain?**
LangGraph is a state machine library built on LangChain. It models the agent loop as a graph
with explicit state, typed reducers, and conditional edges. Plain LangChain chains are linear;
LangGraph supports parallel branches, cycles, and human-in-loop interrupts.
We need all three for the proposal workflow.
Proof: app/runtime/compiler.py — compile_workflow builds a StateGraph

**Q6. How does your Human-in-Loop node work?**
The HumanInLoopAgent calls LangGraph's interrupt() which suspends the graph and serializes
its state to the checkpointer. The API returns a `paused` status with the run_id.
The frontend polls for that status and shows the HITL panel. When the user approves or edits,
the frontend calls POST /workflows/{run_id}/resume. The executor rehydrates the graph
from the checkpointer and resumes from the interrupt point.
Proof: app/nodes/human_in_loop.py, app/runtime/executor.py (_PAUSED_GRAPHS), app/runtime/hitl.py

**Q7. How do you handle parallel branches in LangGraph?**
The section drafters in the proposal workflow run as five parallel branches.
LangGraph's fan-out/fan-in is modeled by adding five edges from the same source node
to the five drafter nodes, then five edges from each drafter to the fan-in node.
The WorkflowState uses an `Annotated[dict, merge_node_outputs]` reducer so parallel
writes from all five nodes merge correctly without clobbering each other.
Proof: app/runtime/state.py (merge_node_outputs), workflows/proposal_generation.yaml (parallel edges)

---

## MCP

**Q8. What is MCP and why does your platform use it?**
MCP (Model Context Protocol) is a standard interface for LLMs to call external tools.
It separates tool definition from tool execution. The MCP server owns the tools;
the LLM calls them via the protocol. This means tool implementations are independent
of the LLM provider.
Proof: app/mcp/server.py (stdio transport, 3 tools), app/nodes/mcp_agent.py

**Q9. How does the MCPAgent prevent the LLM from accessing another session's data?**
The MCPAgent reads session_id from WorkflowState and overwrites the session_id argument
in every tool call before sending it to the MCP server, regardless of what the LLM put there.
The MCP server validates that session_id is present before executing any tool.
This means even a prompt-injected LLM output cannot escalate to another session.
Proof: app/nodes/mcp_agent.py (session_id stomp), app/mcp/server.py (validates before _services())

---

## Evaluation

**Q10. What is LLM-as-a-Judge and what are its failure modes?**
LLM-as-a-Judge uses a second LLM to score another LLM's output against a rubric.
Failure modes: judge anchoring (scores the first criterion it sees and biases the rest),
judge drift (same judge model gives different scores across versions), judge-judge disagreement
(different judge models disagree systematically).
Mitigations: one call per criterion (no anchoring), pin judge model and prompt version in Scorecard,
use discrete 1-5 scale (judges classify better than they estimate floats).
Proof: app/evaluation/judge.py

**Q11. What four criteria does your evaluator score?**
Faithfulness (does the output contradict the retrieved context?),
Relevance (does the output answer the question?),
Completeness (does it cover all required sections?),
Citation Accuracy (are the citations real chunk IDs from Weaviate?).
Proof: app/evaluation/judge.py RUBRICS dict

---

## Observability

**Q12. What is the difference between system observability and model observability?**
System observability answers: is the infrastructure healthy? (Prometheus metrics, Grafana dashboards).
Model observability answers: is the AI quality healthy? (golden set evaluation, scorecard trends).
This platform has both: Prometheus + Grafana for the system, LLM-as-a-Judge for the model.
Proof: app/observability/metrics.py, app/evaluation/

**Q13. Why do you track node latency with a Histogram rather than a Gauge?**
A Gauge shows the current value. A Histogram shows the distribution — p50, p95, p99.
Node latency matters at the tail: a p50 of 1s is fine, a p99 of 30s breaks the user experience.
You cannot see the tail with a Gauge.
Proof: app/observability/metrics.py (NODE_LATENCY Histogram)

---

## Security

**Q14. How does your platform prevent cross-session data leakage?**
[See Q answer in Phase 11 section 5 — Pigeon Holes answer. Say it out loud.]

**Q15. Walk me through a JWT-authenticated request.**
Browser sends Authorization: Bearer <token>.
FastAPI's HTTPBearer extracts the token.
require_role('consultant') dependency calls verify_jwt, decodes the payload, checks role hierarchy.
If role is insufficient → 403. If token is expired → 401. If valid → passes payload to the route handler.
The route handler can extract session_id from the payload for audit logging.
Proof: app/security/rbac.py, app/security/jwt_handler.py

---

## Architecture and Design

**Q16. Why YAML workflow definitions instead of code?**
Separation of concerns: the platform (code) is generic; the use case (YAML) is configuration.
Adding a new workflow is a YAML file, not a code change. Business users or consultants can
read and reason about YAML. Code requires a developer.
Proof: workflows/, app/runtime/compiler.py

**Q17. What is the NodeRegistry and why does it matter for the Builder UI?**
NodeRegistry is a singleton that maps node type names to node classes.
Each node class declares its config_schema as a Pydantic model.
The Builder UI calls GET /node-types, gets the schema, and auto-generates the config form.
Adding a new node type = subclass NodeType + register = it appears in the Builder automatically.
Proof: app/nodes/registry.py, app/api/workflows.py (GET /node-types)

**Q18. You said LangChain is for prompts only. Why not use it for orchestration?**
LangChain's agent abstractions are opinionated and hard to debug. LangGraph gives us explicit
state, explicit edges, and explicit reducers. When something goes wrong I can inspect exactly
what state the graph is in. LangChain chains hide that state.
Proof: app/runtime/ uses LangGraph StateGraph; app/llm/ uses LangChain's ChatPromptTemplate only

**Q19. What is the LLMGateway pattern and why does it matter?**
LLMGateway is an abstract base class with three methods: complete(), complete_structured(),
chat_with_tools(). Every LLM provider implements it. Node code calls the gateway, never
the provider SDK directly. This means swapping providers is a config change (model name in YAML),
not a code change.
Proof: app/llm/base.py, app/llm/registry.py (RegistryLLMGateway dispatches by model prefix)

**Q20. How does your platform store documents without putting bytes in the workflow state?**
Documents are uploaded to MinIO. The ingestion pipeline writes chunks to Weaviate and metadata
to MongoDB. The workflow state carries MinIO keys (strings), not bytes.
When a node needs the document it fetches from MinIO using the key.
This keeps the LangGraph state lean — large objects never serialize into the checkpointer.
Proof: app/storage/minio_client.py, app/runtime/state.py (WorkflowState fields)

---

## Multi-Cloud and Deployment

**Q21. You deployed locally. How would you move to AWS in one sprint?**
1. Push Docker images to ECR.
2. Replace service URLs in .env with managed service endpoints (DocumentDB, ElastiCache, S3, Weaviate Cloud).
3. Add ECS task role with IAM policies for each service.
4. Put ECS tasks behind an ALB with SSL termination.
No code changes. Config change only.
Proof: docs/cloud-migration-map.md

**Q22. What changes between AWS, Azure, and GCP for this stack?**
The container platform (Fargate vs Container Apps vs Cloud Run), the managed database
(DocumentDB vs Cosmos DB vs Atlas), and the identity service (IAM vs Managed Identity vs
Workload Identity). Object storage is protocol-identical (S3 protocol). Redis is wire-identical.
The biggest real difference is identity: AWS uses IAM roles, Azure uses Managed Identity,
GCP uses Workload Identity Federation. The auth code in the app is the same; the cloud
plumbing differs.
Proof: docs/cloud-migration-map.md

---

## Prosus-Specific

**Q23. Prosus Finance AI works on financial data. What would you change about your RAG pipeline for financial documents?**
Three things: (1) chunking strategy — financial tables need table-aware extraction (openpyxl already in stack),
not recursive text splitting; (2) metadata filters — add `document_date` and `fiscal_year` to the
Weaviate schema so retrieval can filter to the correct reporting period;
(3) citation accuracy becomes the primary eval criterion — financial hallucination has regulatory consequences.

**Q24. How would you handle multi-language documents? (Amsterdam context)**
The Weaviate schema already has a `language` field.
text-embedding-3-small supports multilingual embeddings.
Query understanding would detect the language and either translate or search in native language.
The LLM prompt template would specify the output language.

**Q25. What is the hardest bug you hit in this build and what did you learn from it?**
[This is your personal answer. Pick one real bug from the build — the stub drift issue from Phase 10A
is a strong candidate. Frame it as: what the bug was, why it happened, what the fix was,
and what architecture principle it taught you.]

Example: "The three StubLLM classes across three test files drifted out of sync because each was
copy-pasted. Session A's stub expected chat_with_tools; session B's stub didn't have it. The fix
was to move to a single conftest stub that implements the full gateway ABC. The lesson is that
test doubles should live in one place and derive from the same interface as the real code."

---

## Closing Line

"I built this platform to internalize the architecture I will actually build for Eurskem,
and to demonstrate that I can reason about RAG, agentic workflows, evaluation, observability,
and multi-cloud deployment from first principles — not just by calling an API."