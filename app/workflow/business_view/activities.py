"""Business activity aggregation — the replacement for per-node event spam.

Fourteen nodes that ran do not make fourteen things the business did. Four
routers answering "is this safe / is production stopped / how complex / what
kind of request" are one activity: *the system worked out how to handle this*.
This module performs that collapse (§8, §10, §11, §43).

How a node is assigned to an activity, in order of authority:

1. **The workflow says so.** A node carrying `experience.stage_id` (and a
   workflow declaring that stage) is placed in that stage. Presentation
   metadata authored with the process always wins.
2. **Its execution kind says so.** `execution_kind` is declared by every node
   type — human, input, external, ai, deterministic — and already encodes the
   distinction the business cares about: a person decided it, a system of
   record answered it, a model interpreted it, a rule computed it.
3. **Its role within that kind.** Only inside `deterministic` is a node id
   consulted, and then only to separate "who owns this account" routing from
   "what kind of case is this" checks — a grouping distinction, never a
   routing one.

Only nodes that actually ran become activities. A workflow with a hundred
branch endpoints has three or four *activities*, because the ninety-six
branches nobody took are not things that happened.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.workflow.business_view.actions import ActionFactory
from app.workflow.business_view.common import (
    compact_text,
    field_label,
    format_value,
    humanize_case_title,
    humanize_identifier,
    is_empty_value,
    lookup_label,
    parse_handoff_note,
    sentence,
)
from app.workflow.business_view.models import (
    SOURCE_LABELS,
    AIModelUsage,
    ActivityKind,
    BusinessActivityView,
    BusinessFact,
    BusinessRule,
    BusinessSource,
    TechnicalActivityDetail,
    TechnicalNodeRef,
)
from app.workflow.business_view.routers import (
    DECISION_TYPES,
    OWNERSHIP_TERMS,
    ROUTER_TYPES,
    RouterFinding,
    read_router,
)
from app.workflow.business_view.runstate import NodeView, RunView, model_usage

#: Node ids/types whose AI call is "interpreting the incoming request" rather
#: than producing content. Used only to give that activity a better title.
_UNDERSTANDING_TERMS = re.compile(
    r"(understand|interpret|extract|classif|comprehend|triage|intake|read_message)", re.I
)

RECEIVE = "receive"
UNDERSTAND = "understand"
ENRICH = "enrich"
HANDLING = "handling"
OWNERSHIP = "ownership"
AI_WORK = "ai_work"
OUTCOME = "outcome"
DELIVERABLE = "deliverable"


@dataclass(frozen=True)
class _ActivityDef:
    id: str
    title: str
    order: int
    kind: ActivityKind
    #: Present-tense wording used by the status line while this activity runs.
    running_headline: str


_DEFS: dict[str, _ActivityDef] = {
    RECEIVE: _ActivityDef(RECEIVE, "Request received", 10, "workflow", "Receiving the request"),
    UNDERSTAND: _ActivityDef(UNDERSTAND, "Request understood", 20, "ai", "Understanding the request"),
    ENRICH: _ActivityDef(ENRICH, "Business systems checked", 30, "system", "Checking business systems"),
    HANDLING: _ActivityDef(HANDLING, "Handling checks completed", 40, "rule", "Determining how to handle this"),
    OWNERSHIP: _ActivityDef(OWNERSHIP, "Ownership determined", 50, "rule", "Determining who owns this"),
    AI_WORK: _ActivityDef(AI_WORK, "AI work completed", 55, "ai", "Working on the request"),
    OUTCOME: _ActivityDef(OUTCOME, "Case prepared for the owning team", 70, "rule", "Preparing the case"),
    DELIVERABLE: _ActivityDef(DELIVERABLE, "Documents produced", 80, "workflow", "Producing documents"),
}

_HUMAN_ORDER = 60

_STATUS_LABELS = {
    "completed": "Completed",
    "active": "In progress",
    "attention": "Needs attention",
    "planned": "Planned",
    "skipped": "Not needed",
}

_KIND_LABELS: dict[ActivityKind, str] = {
    "ai": "AI",
    "rule": "Business rules",
    "system": "System of record",
    "human": "Person",
    "workflow": "Workflow",
    "mixed": "Mixed",
}


@dataclass
class _Group:
    key: str
    definition: _ActivityDef | None
    title: str
    order: int
    nodes: list[NodeView] = field(default_factory=list)


def _stage_definitions(run: RunView) -> dict[str, tuple[str, int]]:
    """Declared experience stages → (display name, order)."""
    if run.spec is None or run.spec.experience is None:
        return {}
    return {
        stage.id: (stage.display_name, index)
        for index, stage in enumerate(run.spec.experience.stages)
    }


def classify(node: NodeView, stages: dict[str, tuple[str, int]]) -> str:
    """Which business activity this node contributes to."""
    experience = node.spec.experience if node.spec is not None else None
    if experience is not None and experience.stage_id and experience.stage_id in stages:
        return f"stage:{experience.stage_id}"

    kind = node.execution_kind
    if kind == "human":
        # A human checkpoint is never merged into anything: who was asked what,
        # and what they said, is the part of the record that matters most.
        return f"review:{node.node_id}"
    if kind == "input":
        return RECEIVE
    if kind == "output":
        return DELIVERABLE
    if kind == "external":
        return ENRICH
    if kind == "ai":
        searchable = f"{node.node_id} {node.type_name}"
        return UNDERSTAND if _UNDERSTANDING_TERMS.search(searchable) else AI_WORK
    if node.type_name in ROUTER_TYPES or node.type_name in DECISION_TYPES:
        return OWNERSHIP if OWNERSHIP_TERMS.search(node.node_id) else HANDLING
    return OUTCOME


def group_nodes(run: RunView) -> list[_Group]:
    """Bucket every node that ran into ordered activity groups."""
    stages = _stage_definitions(run)
    groups: dict[str, _Group] = {}

    for node in run.nodes:
        if not node.ran:
            continue
        key = classify(node, stages)
        group = groups.get(key)
        if group is None:
            if key.startswith("stage:"):
                display, order = stages[key.split(":", 1)[1]]
                group = _Group(key=key, definition=None, title=display, order=order)
            elif key.startswith("review:"):
                group = _Group(key=key, definition=None, title=node.display_name, order=_HUMAN_ORDER)
            else:
                definition = _DEFS[key]
                group = _Group(key=key, definition=definition, title=definition.title, order=definition.order)
            groups[key] = group
        group.nodes.append(node)

    return sorted(groups.values(), key=lambda g: (g.order, g.key))


def _group_status(nodes: list[NodeView]) -> str:
    statuses = {node.status for node in nodes}
    if "failed" in statuses:
        return "attention"
    if statuses & {"active", "paused"}:
        return "active"
    if statuses and statuses <= {"done", "reused"}:
        return "completed"
    if statuses and statuses <= {"skipped"}:
        return "skipped"
    return "planned"


def _group_kind(group: _Group) -> ActivityKind:
    if group.definition is not None:
        return group.definition.kind
    kinds = {node.execution_kind for node in group.nodes}
    if len(kinds) == 1:
        only = kinds.pop()
        return {
            "ai": "ai", "external": "system", "human": "human",
            "input": "workflow", "output": "workflow",
        }.get(only, "rule")  # type: ignore[return-value]
    return "mixed"


# ---------------------------------------------------------------------------
# Facts contributed by each family of node
# ---------------------------------------------------------------------------


def _source_label(source: BusinessSource, ai: AIModelUsage | None) -> str:
    """A source label that names the model when — and only when — AI ran it.

    §25: a rule-based outcome must never carry a model badge, and an ERP figure
    must never be presented as an AI result.
    """
    if source is BusinessSource.AI and ai is not None and ai.executed:
        return f"AI · {ai.executed}"
    return SOURCE_LABELS[source]


def system_check_fact(node: NodeView) -> BusinessFact:
    """One external lookup, as the business outcome of that lookup.

    Reads only `MCPToolOutput`'s own contract fields — status/found/count and
    the first record — so it works for any MCP tool without knowing what the
    tool does.
    """
    output = node.output_dict()
    status = output.get("status", "ok")
    found = bool(output.get("found"))
    count = output.get("count") or 0
    first = output.get("first") if isinstance(output.get("first"), dict) else {}

    if node.status == "failed" or status in ("error", "denied"):
        display = "The system did not answer"
        missing = True
    elif status == "skipped":
        display = "Not needed"
        missing = True
    elif not found:
        display = "No matching record"
        missing = True
    else:
        # Prefer the record's own human name over its identifier: "BASF SE"
        # tells a salesperson more than "ACC-1043".
        name = next(
            (
                str(first[key])
                for key in ("account_name", "name", "customer_name", "pump_model",
                            "sales_order_reference", "quotation_reference", "title")
                if first.get(key)
            ),
            None,
        )
        if name is None:
            # A record with exactly one populated name has an obvious answer.
            # One with several (an ownership record naming four different
            # roles) does not, and picking one would silently promote it.
            named = [
                str(value) for key, value in first.items()
                if key.endswith("_name") and not is_empty_value(value)
            ]
            name = named[0] if len(named) == 1 else None
        display = name or (f"{count} matching records" if count > 1 else "Found")
        if name and count > 1:
            display = f"{name} (+{count - 1} more matching)"
        missing = False

    return BusinessFact(
        id=f"check:{node.node_id}",
        label=lookup_label(node.node_id),
        value=first or None,
        display=display,
        source=BusinessSource.SYSTEM,
        source_label=SOURCE_LABELS[BusinessSource.SYSTEM],
        node_id=node.node_id,
        missing=missing,
    )


def router_fact(finding: RouterFinding, run: RunView) -> BusinessFact:
    """One deterministic check, in the words of the thing it checked.

    When the router's subject is recoverable (every condition tests the same
    path) the fact is that subject's real value — "Safety issue: No" rather
    than "Safety router: Continue". Otherwise it falls back to the branch name,
    which is at least honest about what happened.
    """
    if finding.subject_key and finding.subject_resolved:
        producer = run.node(finding.subject_node_id) if finding.subject_node_id else None
        source = producer.source if producer is not None else BusinessSource.RULE
        ai = model_usage([producer]) if producer is not None else None
        return BusinessFact(
            id=f"check:{finding.node_id}",
            label=field_label(finding.subject_key),
            value=finding.subject_value,
            display=format_value(finding.subject_value, key=finding.subject_key),
            source=source,
            source_label=_source_label(source, ai),
            node_id=finding.subject_node_id or finding.node_id,
            missing=is_empty_value(finding.subject_value),
        )

    label = humanize_identifier(re.sub(r"_router$", "", finding.node_id))
    return BusinessFact(
        id=f"check:{finding.node_id}",
        label=label,
        value=finding.route,
        display=finding.route_label,
        source=BusinessSource.RULE,
        source_label=SOURCE_LABELS[BusinessSource.RULE],
        node_id=finding.node_id,
    )


def handoff_facts(node: NodeView) -> list[BusinessFact]:
    """Team/owner/customer facts out of a terminal handoff note."""
    note = parse_handoff_note(node.output_dict().get("text"))
    slots = [
        ("team", "Handled by"),
        ("owner", "Owner"),
        ("commercial_owner", "Commercial owner"),
        ("supporting_team", "Supporting team"),
        ("region", "Region"),
        ("customer", "Customer"),
    ]
    return [
        BusinessFact(
            id=f"handoff:{node.node_id}:{slot}",
            label=label,
            value=note[slot],
            display=note[slot],
            source=BusinessSource.RULE,
            source_label=SOURCE_LABELS[BusinessSource.RULE],
            node_id=node.node_id,
        )
        for slot, label in slots
        if note.get(slot)
    ]


def _technical(group: _Group, ai: AIModelUsage | None) -> TechnicalActivityDetail:
    """References into the technical layer — deliberately no payloads (§5, §60)."""
    rules: list[BusinessRule] = []
    for node in group.nodes:
        if node.type_name not in ROUTER_TYPES or not node.succeeded:
            continue
        output = node.output_dict()
        rules.append(
            BusinessRule(
                id=f"{node.node_id}:{output.get('route', '')}",
                name=f"{node.display_name} → {humanize_identifier(str(output.get('route', '')))}",
                description=compact_text(output.get("reason")),
                node_id=node.node_id,
                matched=not output.get("used_fallback", False),
            )
        )

    durations = [node.duration_ms for node in group.nodes if node.duration_ms is not None]
    return TechnicalActivityDetail(
        node_ids=[node.node_id for node in group.nodes],
        nodes=[
            TechnicalNodeRef(
                node_id=node.node_id,
                display_name=node.display_name,
                type_name=node.type_name or None,
                status=node.status_label,
                duration_ms=node.duration_ms,
                error=compact_text(node.error),
            )
            for node in group.nodes
        ],
        ai_calls=[usage for usage in (model_usage([node]) for node in group.nodes) if usage],
        rule_count=len(rules),
        rules=rules,
        duration_ms=sum(durations) if durations else None,
        has_raw_output=any(node.output is not None for node in group.nodes),
    )


def _summary_for(group: _Group, facts: list[BusinessFact]) -> str | None:
    key = group.key
    if key == UNDERSTAND:
        for node in group.nodes:
            output = node.output_dict()
            parsed = output.get("parsed") if isinstance(output.get("parsed"), dict) else None
            result = parsed or (output.get("result") if isinstance(output.get("result"), dict) else {})
            text = compact_text(result.get("english_summary") or result.get("summary"))
            if text:
                return sentence(text)
        return None
    if key == ENRICH:
        answered = [fact for fact in facts if not fact.missing]
        if not facts:
            return None
        return (
            f"{len(answered)} of {len(facts)} checks returned a record."
            if len(answered) != len(facts)
            else f"All {len(facts)} checks answered."
        )
    if key == OUTCOME:
        for node in group.nodes:
            note = parse_handoff_note(node.output_dict().get("text"))
            title = note.get("case_title")
            if title:
                return humanize_case_title(title)
        return None
    if key.startswith("review:"):
        node = group.nodes[0]
        config = (node.spec.config if node.spec is not None else None) or {}
        return compact_text(config.get("question"))
    if key == HANDLING and facts:
        return None
    return None


def build_activities(run: RunView, factory: ActionFactory) -> list[BusinessActivityView]:
    """Every business activity this run has actually performed."""
    findings_by_node = {
        node.node_id: read_router(node, run)
        for node in run.nodes
        if node.type_name in ROUTER_TYPES and node.succeeded
    }

    activities: list[BusinessActivityView] = []
    for group in group_nodes(run):
        ai = model_usage(group.nodes)
        facts: list[BusinessFact] = []

        if group.key == ENRICH:
            facts = [system_check_fact(node) for node in group.nodes]
        elif group.key in (HANDLING, OWNERSHIP):
            facts = [
                router_fact(finding, run)
                for node in group.nodes
                if (finding := findings_by_node.get(node.node_id)) is not None
            ]
        elif group.key == OUTCOME:
            for node in group.nodes:
                facts.extend(handoff_facts(node))

        # Two routers can legitimately share a subject (a check repeated on
        # two branches); showing the same fact twice reads as a bug.
        seen: set[str] = set()
        deduped: list[BusinessFact] = []
        for fact in facts:
            marker = f"{fact.label}={fact.display}"
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(fact)

        status = _group_status(group.nodes)
        starts = [node.started_at for node in group.nodes if node.started_at is not None]
        ends = [node.ended_at for node in group.nodes if node.ended_at is not None]
        durations = [node.duration_ms for node in group.nodes if node.duration_ms is not None]
        kind = _group_kind(group)

        activities.append(
            BusinessActivityView(
                id=group.key,
                title=group.title,
                status=status,  # type: ignore[arg-type]
                status_label=_STATUS_LABELS[status],
                summary=_summary_for(group, deduped),
                kind=kind,
                kind_label=_source_label(BusinessSource.AI, ai) if kind == "ai" else _KIND_LABELS[kind],
                facts=deduped,
                actions=[factory.technical_details(group.key)],
                source_nodes=[node.node_id for node in group.nodes],
                ai=ai,
                started_at=min(
                    (node.started_iso for node in group.nodes if node.started_at is not None),
                    default=None,
                ) if starts else None,
                completed_at=max(
                    (node.ended_iso for node in group.nodes if node.ended_at is not None),
                    default=None,
                ) if ends else None,
                duration_ms=sum(durations) if durations else None,
                technical=_technical(group, ai),
            )
        )

    return activities


def active_activity(activities: list[BusinessActivityView]) -> BusinessActivityView | None:
    return next((activity for activity in activities if activity.status == "active"), None)


def running_headline(activity_id: str) -> str | None:
    definition = _DEFS.get(activity_id)
    return definition.running_headline if definition else None


def outcome_note(run: RunView) -> tuple[NodeView, dict[str, str]] | None:
    """The last terminal handoff note this run produced, if any.

    "Last" rather than "first": a run that reaches several formatting nodes
    (a dual-intent parent plus its branch) ends on the one that describes where
    the case actually went.
    """
    best: tuple[NodeView, dict[str, str]] | None = None
    for node in run.nodes:
        if not node.succeeded or node.execution_kind != "deterministic":
            continue
        if node.type_name in ROUTER_TYPES or node.type_name in DECISION_TYPES:
            continue
        note = parse_handoff_note(node.output_dict().get("text"))
        if note.get("team") or note.get("owner") or note.get("case_title"):
            best = (node, note)
    return best


def find_understanding_node(run: RunView) -> NodeView | None:
    """The AI node that interpreted the incoming request."""
    stages = _stage_definitions(run)
    for node in run.nodes:
        if node.execution_kind == "ai" and node.succeeded and classify(node, stages) == UNDERSTAND:
            return node
    # A workflow that names its extraction node something unexpected still has
    # exactly one AI node whose output carries a structured payload.
    for node in run.nodes:
        if node.execution_kind != "ai" or not node.succeeded:
            continue
        output = node.output_dict()
        if isinstance(output.get("parsed"), dict) or isinstance(output.get("result"), dict):
            return node
    return None


def extraction_payload(node: NodeView | None) -> dict[str, Any]:
    """The structured fields an extraction node produced.

    `parsed` (TransformAgent) and `result` (the older extraction shape) are the
    two forms in use. `raw` — the model's own JSON string — is deliberately
    never returned: it is exactly what the Business View must stop showing.
    """
    if node is None:
        return {}
    output = node.output_dict()
    for key in ("parsed", "result"):
        value = output.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}
