# Scalable Preflight Validation for 200+ Node Types — Design

**Status:** design proposal (builds on the existing engine in
`app/runtime/preflight.py` and the node contract in `app/nodes/base.py`).

**Goal:** 200, 500, 1,000 node types must stay manageable. Complexity
lives in standardized node definitions and reusable policies — never in
pair-by-pair code. Adding node #501 means writing one definition, not 500
compatibility rules.

---

## 1. Recommended architecture

Three layers, the same shape the existing engine already approximates:

```text
+--------------------------------------------------------------+
|  Layer 1 - Node contracts (one per node type)                |
|  NodeDefinition: schemas + capability tags + constraints     |
|  200 definitions, zero pair logic                            |
+--------------------------------------------------------------+
|  Layer 2 - Generic preflight engine                          |
|  Universal rules computed from contracts:                    |
|  schema compatibility, capability availability, execution    |
|  modes, permissions, limits, topology                        |
+--------------------------------------------------------------+
|  Layer 3 - Policy library (small, reusable, composable)      |
|  RequiresStructuredOutput / NoStreamingInput /               |
|  MaxPayloadSize(10MB) / HumanGateBeforeWrite / ...           |
|  Attached per node via contract, evaluated generically       |
+--------------------------------------------------------------+
```

The number of possible pairs (19,900 undirected / 39,800 directed at 200
nodes) is **never materialized as code** — it is at most a cached matrix
of *computed results*, derived on demand from the contracts.

Mapping to this repository today:

| Design layer | Existing implementation | Gap to close |
| --- | --- | --- |
| Node contract | `NodeType` ClassVars: `config_schema`, `input_schema`, `output_schema`, `required_services()`, `preflight_output_fields()`, `about` | Add capability tags (`produces`/`accepts`/`supports`) and declarative constraints |
| Generic engine | `preflight_workflow_spec()` — template resolution, upstream reachability, service availability, model catalog, fan-in safety | Add edge data-type compatibility + capability checks as universal rules |
| Policies | Implicit (e.g. write-needs-approval in `ExternalActionAgent`) | Extract into a named, composable policy registry |

## 2. Standardized node contract

```python
class NodeDefinition(BaseModel):
    # Identity
    type_name: str                      # registry key, stable forever

    # Data contract (the existing three schemas)
    config_schema: type[BaseModel]      # extra="forbid"
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]

    # Capability tags - the scalable replacement for pair rules
    produces: set[DataType]             # {text, json, image, file, table, ...}
    accepts: set[DataType]              # what inbound edges may carry
    requires_capabilities: set[str]     # llm, email, object_store, mcp:*, ...
    provides_capabilities: set[str]     # rare; e.g. a node that caches creds

    # Execution envelope
    execution_kind: ExecutionKind       # ai | deterministic | external | human | io
    streaming: bool = False
    async_safe: bool = True
    max_payload_bytes: int | None = None
    rate_limit: RateLimit | None = None

    # Environment / auth
    requires_auth: bool = False
    requires_environment: set[str] = set()   # env vars / services
    permissions: set[str] = set()            # RBAC permissions to run

    # Exceptions without pair logic
    validators: list[PolicyRef] = []    # named, composable policies
    incompatible_with: set[str] = set() # last-resort explicit bans (rare, audited)
    version: str = "1"
```

**Rules for the contract:**

1. `produces`/`accepts` use a **closed vocabulary** of data types
   (`text`, `json`, `table`, `image`, `file`, `audio`, `number`,
   `record:<entity>` ...). Growing the vocabulary is an engine-level
   decision, not a per-node decision.
2. `incompatible_with` is deliberately rare and must carry a reason;
   a CI lint flags any node that uses more than N bans (smell: the
   contract is missing a capability tag).
3. Schemas remain the source of truth for *field-level* compatibility;

## 3. Universal preflight rules (engine order)

For every edge `source → target` in a workflow:

```text
R1 Data-type match      source.produces ∩ target.accepts ≠ ∅
R2 Schema compatibility source.output_schema ⟶ target.input_schema
                        (field-name/typing match for the fields the target
                         actually binds; unknown bindings are errors)
R3 Capability supply    target.requires_capabilities ⊆
                        (services ∪ upstream provides_capabilities)
R4 Execution mode       e.g. sync-only consumers reject streaming-only
                        sources; human gates allowed only where the graph
                        can pause
R5 Permissions          node.permissions ⊆ caller role
R6 Environment          requires_environment ⊆ configured environment
R7 Limits               upstream declared payload ≤ target.max_payload_bytes
                        when statically knowable
R8 Policies             every policy in source.validators ∪ target.validators
                        evaluates clean
R9 Explicit bans        target.type_name ∉ source.incompatible_with
                        (symmetric check)
```

Every rule is a pure function `(source_def, target_def, context) → list[Issue]`.
The engine iterates rules; nodes never see each other's identities. That is
what makes the 201st node free: it is checked against the same nine rules.

## 4. Compatibility algorithm

```python
def preflight_edge(
    source: NodeDefinition,
    target: NodeDefinition,
    context: PreflightContext,          # services, env, caller role
) -> EdgeResult:
    issues: list[Issue] = []
    for rule in UNIVERSAL_RULES:        # R1..R9, ordered cheap→expensive
        issues.extend(rule(source, target, context))
        if issues and rule.fatal:
            break                       # short-circuit on first fatal
    policies = registry.policies_for(source, target)
    for policy in policies:
        issues.extend(policy.evaluate(source, target, context))
    return EdgeResult(source.type_name, target.type_name, issues)
```

Workflow preflight = topology checks (existing engine: reachability,
guaranteed-before, fan-in safety, cycles) + `preflight_edge` for each
declared edge + per-node static checks (config schema, model catalog,
template resolution — all already implemented).

**Cost:** E edges × 9 rules × O(schema fields). For a 1,000-node catalog,
an exhaustive all-pairs audit is ~10⁶ edge checks — seconds when schema
comparison is memoized. A CI batch job, not a request-path cost.

## 5. Example implementation (fits this repo)

```python
# app/nodes/contract.py — the capability layer
class DataType(str, Enum):
    TEXT = "text"; JSON = "json"; TABLE = "table"; IMAGE = "image"
    FILE = "file"; AUDIO = "audio"; NUMBER = "number"

# app/nodes/registry.py — registration stays exactly as today
@NodeRegistry.register
class TextSummarizer(NodeType):
    type_name = "TextSummarizer"
    produces = {DataType.TEXT, DataType.JSON}
    accepts = {DataType.TEXT}
    requires_capabilities = {"llm", "cost_ledger"}
    execution_kind = "ai"
    validators = [PolicyRef("MaxPayloadSize", limit=2_000_000)]
    ...
```

```python
# app/runtime/preflight_edges.py — one universal rule
def rule_data_type_match(src, dst, ctx) -> list[Issue]:
    if src.produces & dst.accepts:
        return []
    return [Issue(
        code="EDGE_NO_COMPATIBLE_DATA_TYPE",
        severity=ERROR,
        message=(
            f"{src.type_name} produces "
            f"{sorted(p.value for p in src.produces)} but "
            f"{dst.type_name} accepts only "
            f"{sorted(a.value for a in dst.accepts)}."
        ),
        suggestion="Insert a transform node or change the binding.",
    )]
```

The error message is exactly the requested shape:

```text
FAIL
Source: ImageGeneratorNode
Target: TextProcessorNode
Reason:
  Target accepts: text, json
  Source produces: image
  No compatible data type exists.
```

## 6. Ten example node types and what the engine infers

| Node | produces | accepts | requires | Inferred results |
| --- | --- | --- | --- | --- |
| StartAgent | text, file | — | — | can feed any text/file consumer |
| EmailReader | text, file | — | email | needs email service configured |
| TextSummarizer | text | text | llm | ✓ after any text producer |
| Translator | text | text | llm | ✓ chains freely with text nodes |
| ImageGenerator | image | text | llm-image | ✗ into TextSummarizer (R1) |
| OCR | text | image | vision | ✓ after ImageGenerator, ✗ after TextSummarizer |
| TableExtractor | table | file | llm | ✓ after EmailReader attachments |
| HumanInLoopAgent | decision, text | any | — | pause-capable; R4 gates placement |
| CRMWriter | record:crm | json | mcp:crm + approval policy | R8 enforces the gate |
| EndAgent | — | any | — | universal sink |

No pair was written down; every ✓/✗ falls out of R1–R9.

## 7. Exception/constraint system (policies, not ifs)

```python
class Policy(Protocol):
    name: ClassVar[str]
    def evaluate(self, src, dst, ctx) -> list[Issue]: ...

class MaxPayloadSize:
    name = "MaxPayloadSize"
    def __init__(self, limit: int): self.limit = limit
    ...

class HumanGateBeforeWrite:
    """Every path into a write-class node must traverse a human gate."""
    ...

POLICY_REGISTRY: dict[str, type[Policy]] = {}
def register_policy(cls): POLICY_REGISTRY[cls.name] = cls; return cls
```

Properties that keep this generic:

- Policies are **node-agnostic** — they read contracts, never node ids.
- Policies are **attached declaratively** (`validators:` in the contract)
  or **globally** by capability (e.g. every node with `produces:record:*`
  automatically gets `HumanGateBeforeWrite`).
- A policy that needs one specific pair of nodes is a smell; it must be
  reviewed as a candidate for a missing capability tag instead.


## 8. Automated all-pairs testing

```python
# tests/test_node_pair_matrix.py
def test_all_pairs_evaluate_without_crashing():
    defs = NodeRegistry.contracts()
    for source in defs:
        for target in defs:
            if source is target:
                continue
            result = preflight_edge(source, target, TEST_CONTEXT)
            assert result.error is None          # engine never crashes
            assert result.verdict in (PASS, FAIL)

def test_contracts_are_complete():
    for d in NodeRegistry.contracts():
        assert d.produces, f"{d.type_name} declares no produces tags"
        assert d.accepts or d.is_source
        assert d.version

def test_no_contradictory_capabilities():
    for d in NodeRegistry.contracts():
        assert not (d.streaming and not d.async_safe)

def test_pair_decisions_are_stable(snapshot):
    """The PASS/FAIL matrix is a snapshot: a change to any universal rule
    surfaces as an explicit diff of affected pairs, not a surprise."""
    snapshot.assert_match(compute_matrix_digest())
```

**Distinguishing the four outcomes:**

| Outcome | How it is identified |
| --- | --- |
| Valid connection | R1–R9 + policies all clean |
| Invalid by design | deterministic FAIL with a stable rule code (R1, R8…) |
| Preflight engine bug | exception/crash in a rule, or a pair whose verdict flips without contract changes (snapshot catches it) |
| Incomplete definition | contract-completeness test fails (missing tags/schemas) — reported per node, before any pair check |

## 9. Error-reporting design

Reuse the existing `PreflightIssue` shape (`code`, `severity`, `message`,
`path`, `node_id`, `suggestion`) with a new `EDGE_*` code family:

```text
EDGE_NO_COMPATIBLE_DATA_TYPE   (R1, error)
EDGE_SCHEMA_MISMATCH           (R2, error, lists the offending fields)
EDGE_MISSING_CAPABILITY        (R3, error, names the missing service)
EDGE_EXECUTION_MODE_CONFLICT   (R4, error)
EDGE_PERMISSION_MISSING        (R5, error)
EDGE_ENVIRONMENT_MISSING       (R6, error)
EDGE_PAYLOAD_TOO_LARGE         (R7, warning/error by knowability)
EDGE_POLICY_VIOLATION          (R8, severity chosen by the policy)
EDGE_EXPLICIT_INCOMPATIBLE     (R9, error, must print the ban's reason)
```

Every edge issue carries `source_node_id`, `target_node_id`, and the
edge's YAML path so the Builder can underline the exact wire.

## 10. Caching & performance

Prioritized by actual need (simplicity first):

1. **Schema-pair memoization** — `(source_type, target_type) → R2 result`
   is pure; cache it per process. This is the only expensive rule.
2. **Capability indexes** — invert the catalog once
   (`produces_index: DataType → set[type]`, `accepts_index: …`) so the
   Builder palette can answer "which nodes can follow X?" in O(1) lookups.
3. **Snapshot digest of the all-pairs matrix** — computed in CI (seconds),
   stored as a hash; only pairs whose contracts changed are re-checked on
   incremental runs.
4. **Not needed yet:** parallel validation, distributed caches, dependency
   graphs across workflows. At 1,000 node types the all-pairs audit is
   ~10⁶ cheap pure-function calls; measure before adding machinery.


## 11. Adding a node without touching the engine

```python
@NodeRegistry.register
class PdfToText(NodeType):
    type_name = "PdfToText"
    produces = {DataType.TEXT}
    accepts = {DataType.FILE}
    requires_capabilities = {"object_store"}
    execution_kind = "deterministic"
    config_schema = PdfToTextConfig      # extra="forbid"
    input_schema = FileInput
    output_schema = TextOutput
```

That is the entire change. The engine infers: it can follow any
`produces:file` node, it can feed any `accepts:text` node, it needs the
object store, and it participates in the all-pairs snapshot. No engine
edit, no pair rules — enforced by `test_contracts_are_complete` and the
discovery autotest (`tests/test_node_preflight_coverage.py` pattern).

## 12. Scaling to 500–1,000+ nodes

- Rules stay O(1) per pair; the catalog is data, so growth is linear in
  definitions, constant in engine code.
- Split the catalog into **families/packages** (`nodes/core`, `nodes/ai`,
  `nodes/integrations/<provider>`) — discovery already auto-imports
  modules; family-level policies (e.g. everything in `integrations/`
  gets `IdempotencyRequired`) attach by path, not by name.
- The Builder palette groups by `family`/category (already implemented) —
  1,000 nodes stay navigable because users see ~10 categories, not a list.
- The all-pairs snapshot becomes a sharded CI job per family if it ever
  exceeds ~30 s (it will not below several thousand types).

## 13. Common architectural mistakes to avoid

1. **A hand-maintained compatibility matrix** (the 39,800-entry trap).
   The matrix must always be *derived*; if a human edits a cell, the
   design is broken.
2. **Node-id-specific branches in the engine** (`if source == "Node17"`).
   Ban by code review + a lint that greps the engine for node literals.
3. **Free-form tags without a vocabulary** — `produces: "maybe-text"`
   defeats R1. The DataType enum is closed and versioned.
4. **Warnings-by-default for real errors** — noisy preflight trains
   authors to ignore it (this repo already treats this seriously:
   `--warnings-as-errors` in CI).
5. **Running all-pairs on the request path** — it belongs in CI and the
   Builder's cached palette, never in workflow launch.
6. **Letting policies mutate state** — policies must be pure readers of
   contracts, or the engine becomes untestable.
7. **One mega-schema for config** — each node owns its own
   `extra="forbid"` schema; shared fragments compose via Pydantic mixins.

## 14. Recommended module layout (in this repo)

```text
app/nodes/
    base.py                 # NodeType + contract ClassVars (extend here)
    contract.py             # DataType enum, NodeDefinition, PolicyRef
    registry.py             # NodeRegistry + contract manifest
    policies/
        __init__.py         # POLICY_REGISTRY + register_policy
        payload.py          # MaxPayloadSize, MaxFileSize
        human_gate.py       # HumanGateBeforeWrite, ApprovalChain
        streaming.py        # NoStreamingInput, RequiresStreaming
    core/                   # Start/End/Literal/Transform/Router/Join...
    ai/                     # AITask, research, renderers
    integrations/
        email/  dynamics/  d365_finance/  business_records/  files/
app/runtime/
    preflight.py            # topology + templates + services (existing)
    preflight_edges.py      # UNIVERSAL_RULES R1-R9 + preflight_edge()
    preflight_matrix.py     # all-pairs audit + snapshot digest (CI use)
tests/
    test_node_pair_matrix.py
    test_contract_completeness.py
    test_policy_registry.py
scripts/
    preflight_workflows.py  # existing zero-token gate (unchanged)
    audit_node_matrix.py    # CI entry point for the all-pairs snapshot
```

---

## Bottom line

The existing engine already refuses the pair-by-pair design: templates,
services, schemas, and fan-in are all checked generically from node
metadata. The step to 200+ node types is to (1) add capability tags to
the contract, (2) express the nine universal rules — most of which are
thin — and (3) move today's scattered special cases into named policies.
After that, the cost of node #501 is one file and one test run, and the
39,800 pairs are a cached computation, not a line of code.

   capability tags decide *edge-level* compatibility.
