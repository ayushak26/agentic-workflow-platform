"""The Business View contract — the shapes a business user's screen is built from.

These models are the whole point of the Business View redesign. The previous
projection handed React a thin re-labelling of *execution* (node ids, per-node
start/complete events, the extraction node's raw JSON) and left the UI to
reconstruct meaning from event names. Everything here is the opposite: the
server decides what a business activity is, which facts matter, where each
fact came from, what is missing, what a person may do about it, and what
happens next. React renders; it does not infer.

Two rules run through every model:

* **Nothing is invented.** Every value traces to run data, workflow output, or
  workflow metadata. A field the platform cannot know is absent, not guessed.
* **Raw model output never appears here.** `raw`/`parsed` blobs, prompts and
  per-node payloads live behind the technical-detail endpoint
  (app/api/runs.py::run_business_technical_detail) and in Cockpit. See
  `TechnicalActivityDetail`, which deliberately carries *references* to that
  data rather than the data itself.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class BusinessSource(str, Enum):
    """Where a displayed result actually came from.

    Kept explicit because conflating these is the single most misleading thing
    a UI like this can do: a deterministic route shown with a model badge
    makes the platform look like it guessed, and an ERP figure shown as an AI
    result makes a verified number look like a guess.
    """

    AI = "ai"                            # a model produced it
    RULE = "rule"                        # a deterministic router/decision rule produced it
    SYSTEM = "system"                    # an external system of record (ERP/CRM/MCP tool)
    HUMAN = "human"                      # a person entered, corrected or approved it
    MESSAGE = "customer_message"         # stated verbatim by the customer
    WORKFLOW = "workflow"                # workflow configuration/structure


#: Human-facing label per source, used when no more specific detail exists
#: (an AI source is always narrowed to the model that actually executed).
SOURCE_LABELS: dict[BusinessSource, str] = {
    BusinessSource.AI: "AI interpreted",
    BusinessSource.RULE: "Business rule",
    BusinessSource.SYSTEM: "System of record",
    BusinessSource.HUMAN: "Entered by a person",
    BusinessSource.MESSAGE: "Customer message",
    BusinessSource.WORKFLOW: "Workflow",
}


class BusinessActionType(str, Enum):
    """The closed set of typed platform actions a Business View button may invoke.

    A button is never wired to a free-form prompt or an arbitrary endpoint: the
    UI sends one of these types plus validated params, and
    app/workflow/business_view/actions.py maps it to a real handler. A type
    with no handler is never emitted, so the screen cannot show a control that
    does nothing.
    """

    # Run control (existing durable primitives)
    PAUSE_RUN = "pause_run"
    RESUME_RUN = "resume_run"
    STOP_RUN = "stop_run"
    RERUN_DEPENDENCY = "rerun_dependency"          # "Recheck" — the safe-retry primitive
    APPROVE = "approve"
    REJECT = "reject"
    ASSIGN_WORK_ITEM = "assign_work_item"

    # Working with what the AI understood
    EDIT_FACT = "edit_fact"
    EXPLAIN_DECISION = "explain_decision"          # "Why?"
    DRAFT_CLARIFICATION = "draft_clarification"    # "Ask customer" — drafts, never sends
    ADD_NOTE = "add_note"
    ROUTE_OVERRIDE = "route_override"              # a person overrides the handling decision

    # Looking things up
    RELATED_RECORD_LOOKUP = "related_record_lookup"  # read a named record from a system of record
    DOCUMENT_REVIEW = "document_review"              # open/preview an attached document
    OPEN_RELATED_RECORD = "open_related_record"      # client-side navigation to a record view
    OPEN_TECHNICAL_DETAILS = "open_technical_details"
    ASK_AI = "ask_ai"


#: Action types the client performs itself (navigation / opening a panel).
#: They still travel as typed actions so permission and state gating stay in
#: one place, but they are never dispatched to the action endpoint.
CLIENT_SIDE_ACTIONS = {
    BusinessActionType.OPEN_TECHNICAL_DETAILS,
    BusinessActionType.OPEN_RELATED_RECORD,
    BusinessActionType.DOCUMENT_REVIEW,
    BusinessActionType.EDIT_FACT,
    BusinessActionType.ASK_AI,
    BusinessActionType.APPROVE,
    BusinessActionType.REJECT,
    # Both rerun primitives create a *new* run and navigate to it, which the
    # client already knows how to do (POST .../retry and .../restart).
    BusinessActionType.RERUN_DEPENDENCY,
}


class BusinessAction(BaseModel):
    """One button. Typed, permission-checked, and valid for the current state.

    `enabled=False` with a `disabled_reason` is used only where hiding the
    control would be *more* confusing than showing why it is unavailable
    (§27: never show a button that is invalid, but do explain a control a user
    expects to find).
    """

    id: str
    type: BusinessActionType
    label: str
    description: str | None = None
    emphasis: Literal["primary", "secondary", "danger"] = "secondary"
    enabled: bool = True
    disabled_reason: str | None = None
    #: True when the platform will prepare the change and ask for approval
    #: rather than applying it directly (§54).
    requires_approval: bool = False
    #: Validated arguments for the typed handler — never free text destined
    #: for a prompt.
    params: dict[str, Any] = Field(default_factory=dict)


class BusinessFact(BaseModel):
    """One business-language value with its provenance.

    `display` is what the UI prints; `value` is the underlying value kept for
    editing and for round-tripping into typed actions.
    """

    id: str
    label: str
    value: Any = None
    display: str
    source: BusinessSource
    source_label: str
    node_id: str | None = None
    editable: bool = False
    #: Computed before a fact it depends on was corrected (see
    #: app/workflow/fact_corrections.py).
    stale: bool = False
    confidence: float | None = None
    #: The workflow looked for this and it is not present — rendered as a
    #: muted "Not stated" rather than dropped, because an absent commercial
    #: fact is itself information.
    missing: bool = False
    actions: list[BusinessAction] = Field(default_factory=list)


class AIModelUsage(BaseModel):
    """Which model actually ran, alongside what was asked for (§22–§24).

    `executed` is the authority for the compact badge. `requested`/`selected`
    exist because automatic routing and provider fallback mean the model named
    in the YAML is frequently not the model that answered — and hiding that
    makes an evaluation-driven router impossible to trust. Every field is
    None when the platform did not record it; nothing is filled in by guess.
    """

    requested: str | None = None
    selected: str | None = None
    executed: str | None = None
    fallback: bool = False
    fallback_reason: str | None = None
    routing_reason: str | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    task_type: str | None = None
    provider: str | None = None
    call_count: int = 0


class BusinessRule(BaseModel):
    """One deterministic rule that fired, in the author's own words."""

    id: str
    name: str
    description: str | None = None
    node_id: str | None = None
    matched: bool = True


class TechnicalNodeRef(BaseModel):
    """A pointer into the technical layer — never the technical payload itself."""

    node_id: str
    display_name: str
    type_name: str | None = None
    status: str
    duration_ms: int | None = None
    error: str | None = None


class TechnicalActivityDetail(BaseModel):
    """What "Technical details" reveals for one business activity (§47).

    Counts and identifiers only. The raw model output, parsed payload and
    prompts are fetched separately and on demand, so the default Business View
    payload cannot contain them even by accident (§5, §60).
    """

    node_ids: list[str] = Field(default_factory=list)
    nodes: list[TechnicalNodeRef] = Field(default_factory=list)
    ai_calls: list[AIModelUsage] = Field(default_factory=list)
    rule_count: int = 0
    rules: list[BusinessRule] = Field(default_factory=list)
    duration_ms: int | None = None
    has_raw_output: bool = False


ActivityStatus = Literal["planned", "active", "completed", "attention", "skipped"]
ActivityKind = Literal["ai", "rule", "system", "human", "workflow", "mixed"]


class BusinessActivityView(BaseModel):
    """Several technical nodes, presented as the one thing the business did.

    "Determine handling" is a single activity even when four routers produced
    it, and it appears once — not as eight start/completed events (§8, §43).
    """

    id: str
    title: str
    status: ActivityStatus
    status_label: str
    summary: str | None = None
    kind: ActivityKind
    kind_label: str
    facts: list[BusinessFact] = Field(default_factory=list)
    actions: list[BusinessAction] = Field(default_factory=list)
    source_nodes: list[str] = Field(default_factory=list)
    ai: AIModelUsage | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    technical: TechnicalActivityDetail = Field(default_factory=TechnicalActivityDetail)


AttentionSeverity = Literal["blocking", "warning", "info"]


class BusinessAttentionItem(BaseModel):
    """One thing a person should look at, with the ways to resolve it (§6, §7).

    `actions` is the difference between an attention centre and a list of
    complaints: every item that the platform can offer a route out of, does.
    """

    id: str
    title: str
    detail: str | None = None
    severity: AttentionSeverity
    status_label: str
    field: str | None = None
    actions: list[BusinessAction] = Field(default_factory=list)


StatusTone = Literal["progress", "attention", "blocked", "waiting", "done", "stopped"]


class BusinessStatusView(BaseModel):
    """The authoritative business status (§12, §13).

    Derived from workflow state — run status, pause kind, the active business
    activity, the handling outcome — never from the latest timeline event and
    never from a model. A narrator may only rephrase `headline`/`summary`;
    `code` and `tone` are not negotiable.
    """

    code: str
    headline: str
    summary: str
    tone: StatusTone
    attention_count: int = 0
    #: "deterministic" until a validated narration replaces the wording.
    narration_source: Literal["deterministic", "ai"] = "deterministic"
    narration_model: str | None = None
    #: Changes only when something a business user would notice changed —
    #: the narration cache key (§17).
    state_version: str = ""


class BusinessUnderstanding(BaseModel):
    """"What I understood" as business fields, never as JSON (§4)."""

    node_id: str | None = None
    summary: str | None = None
    confidence: float | None = None
    fields: list[BusinessFact] = Field(default_factory=list)
    source: BusinessSource = BusinessSource.AI
    source_label: str = SOURCE_LABELS[BusinessSource.AI]
    ai: AIModelUsage | None = None
    actions: list[BusinessAction] = Field(default_factory=list)


class BusinessDecisionView(BaseModel):
    """The handling decision, made visually important (§19, §20)."""

    id: str
    headline: str
    summary: str | None = None
    reason: str | None = None
    source: BusinessSource = BusinessSource.RULE
    source_label: str = SOURCE_LABELS[BusinessSource.RULE]
    facts: list[BusinessFact] = Field(default_factory=list)
    rules: list[BusinessRule] = Field(default_factory=list)
    actions: list[BusinessAction] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    #: True when a person overrode the automatic route.
    overridden: bool = False
    overridden_by: str | None = None
    overridden_at: str | None = None
    original_headline: str | None = None
    stale: bool = False


class BusinessNextStep(BaseModel):
    """"What happens next" — always present, blocked or not (§30)."""

    headline: str
    description: str | None = None
    blocked: bool = False
    blocked_reason: str | None = None
    owner: str | None = None
    actions: list[BusinessAction] = Field(default_factory=list)


class BusinessRelatedRecord(BaseModel):
    """A record in another system this work item refers to (§35, §37)."""

    id: str
    kind: str
    label: str
    reference: str
    source: BusinessSource
    source_label: str
    actions: list[BusinessAction] = Field(default_factory=list)


class BusinessAttachment(BaseModel):
    """A real file input on this run — never an attachment merely mentioned in
    prose, which the platform cannot open (§36)."""

    id: str
    name: str
    kind: str
    size_bytes: int | None = None
    file_key: str | None = None
    actions: list[BusinessAction] = Field(default_factory=list)


TimelineKind = Literal["activity", "human", "failure", "edit", "status", "override"]


class BusinessTimelineEntry(BaseModel):
    """One business-meaningful moment.

    Node start/completion pairs are aggregated away (§8, §9). Human decisions,
    edits, overrides and failures stay individually visible because those are
    exactly the moments a person is accountable for (§67).
    """

    id: str
    ts: str
    title: str
    detail: str | None = None
    #: Short "✓ …" supporting lines shown under the entry.
    marks: list[str] = Field(default_factory=list)
    kind: TimelineKind = "activity"
    source: BusinessSource | None = None
    source_label: str | None = None


class BusinessWorkItem(BaseModel):
    id: str
    title: str
    type: str
    reference: str
    started_at: str | None = None
    updated_at: str | None = None
    assigned_to: str | None = None
    customer: str | None = None


class BusinessRequiredUserAction(BaseModel):
    """A pending human gate, kept as its own field because the HITL review
    panel is a distinct, already-tested surface the Business View embeds."""

    type: Literal["approval_review", "resume_decision"]
    node_id: str | None = None
    question: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    message: str | None = None


class BusinessProcess(BaseModel):
    name: str
    goal: str


class BusinessProjection(BaseModel):
    """Everything one Work Item screen needs, in business language (§45).

    Ordered to match the visual priority the screen must express (§40):
    attention, status, decision, next step, then facts and completed work.
    """

    work_item: BusinessWorkItem
    process: BusinessProcess
    #: Raw run status, kept for the UI's own reconnect/polling logic.
    status: str
    business_status: BusinessStatusView
    attention: list[BusinessAttentionItem] = Field(default_factory=list)
    understanding: BusinessUnderstanding = Field(default_factory=BusinessUnderstanding)
    activities: list[BusinessActivityView] = Field(default_factory=list)
    #: The short "What happened" checklist (§18).
    happened: list[str] = Field(default_factory=list)
    facts: list[BusinessFact] = Field(default_factory=list)
    decision: BusinessDecisionView | None = None
    recommended_actions: list[BusinessAction] = Field(default_factory=list)
    other_actions: list[BusinessAction] = Field(default_factory=list)
    next_step: BusinessNextStep | None = None
    related_records: list[BusinessRelatedRecord] = Field(default_factory=list)
    attachments: list[BusinessAttachment] = Field(default_factory=list)
    timeline: list[BusinessTimelineEntry] = Field(default_factory=list)
    #: Every action the current user may take on this work item right now.
    allowed_actions: list[BusinessAction] = Field(default_factory=list)
    required_user_actions: list[BusinessRequiredUserAction] = Field(default_factory=list)
    #: Prompt chips above the conversation input, derived from state (§31).
    suggested_questions: list[str] = Field(default_factory=list)
    #: Counts for the "N business activities completed" line (§10).
    activity_summary: dict[str, int] = Field(default_factory=dict)
