"""One normalised view of a run, shared by every part of the projection.

The run document, the workflow spec, the node-type registry and the cost
ledger each know a different piece of "what happened". Assembling them once —
here — keeps the activity, status, attention, decision and action modules
reading from a single consistent picture instead of each re-deriving node
status from `node_runs` in a slightly different way (which is how the previous
projection ended up disagreeing with itself about whether a node had run).

Nothing in this module performs I/O. The cost-ledger entries are passed in by
the API route, which is the only layer allowed to touch a database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from functools import lru_cache

from app.nodes.categories import execution_kind_for
from app.nodes.registry import NodeRegistry
from app.runtime.schema import NodeSpec, WorkflowSpec
from app.workflow.business_view.common import (
    duration_ms,
    humanize_identifier,
    timestamp_iso,
)
from app.workflow.business_view.models import AIModelUsage, BusinessSource

#: Node execution kinds → the provenance a result from that node carries.
#: `execution_kind` is a platform contract declared by every node type
#: (app/nodes/categories.py), so provenance cannot drift out of sync with what
#: a node actually does — which is the whole point of §21/§25.
_KIND_TO_SOURCE = {
    "ai": BusinessSource.AI,
    "deterministic": BusinessSource.RULE,
    "external": BusinessSource.SYSTEM,
    "human": BusinessSource.HUMAN,
    "input": BusinessSource.MESSAGE,
    "output": BusinessSource.WORKFLOW,
}

_STATUS_LABEL = {
    "pending": "Planned",
    "active": "In progress",
    "done": "Completed",
    "reused": "Completed from saved work",
    "paused": "Waiting for you",
    "failed": "Needs attention",
    "skipped": "Not needed",
}

_TERMINAL_RUN_STATUSES = {"completed", "failed", "rejected"}


@lru_cache(maxsize=1)
def _execution_kinds() -> dict[str, str]:
    """type_name → execution_kind, read from the node registry itself.

    The registry derives `uses_llm` from each node type's own
    `required_services` declaration, so a node that starts calling a model is
    reclassified automatically. Cached because the registry is populated once
    at import and never changes at runtime.
    """
    return {entry["type_name"]: entry["execution_kind"] for entry in NodeRegistry.manifest()}


def execution_kind_of(type_name: str) -> str:
    if not type_name:
        return "deterministic"
    known = _execution_kinds().get(type_name)
    return known or execution_kind_for(type_name, uses_llm=False)


@dataclass
class NodeView:
    """One workflow node as the Business View needs to see it."""

    node_id: str
    type_name: str
    execution_kind: str
    status: str                       # pending|active|done|reused|paused|failed|skipped
    output: Any = None
    started_at: float | None = None
    ended_at: float | None = None
    error: str | None = None
    display_name: str = ""
    spec: NodeSpec | None = None
    model_selections: list[dict[str, Any]] = field(default_factory=list)
    cost_entries: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ran(self) -> bool:
        return self.status in ("done", "reused", "failed", "paused", "active")

    @property
    def succeeded(self) -> bool:
        return self.status in ("done", "reused")

    @property
    def source(self) -> BusinessSource:
        return _KIND_TO_SOURCE.get(self.execution_kind, BusinessSource.WORKFLOW)

    @property
    def status_label(self) -> str:
        return _STATUS_LABEL.get(self.status, self.status)

    @property
    def duration_ms(self) -> int | None:
        return duration_ms(self.started_at, self.ended_at)

    @property
    def started_iso(self) -> str | None:
        return timestamp_iso(self.started_at)

    @property
    def ended_iso(self) -> str | None:
        return timestamp_iso(self.ended_at)

    def output_dict(self) -> dict[str, Any]:
        return self.output if isinstance(self.output, dict) else {}


def _node_status(node_id: str, run_doc: dict[str, Any]) -> str:
    """Business status for one node, from the durable record.

    `node_runs` is authoritative while a run is live. A node with a recorded
    output but no `node_runs` entry ran in an earlier attempt and was reused;
    a node with neither, on a finished run, was never reached — "Not needed",
    not "Planned", because nothing is going to happen now.
    """
    record = (run_doc.get("node_runs") or {}).get(node_id) or {}
    mapped = {
        "running": "active",
        "paused": "paused",
        "completed": "done",
        "reused": "reused",
        "failed": "failed",
    }.get(record.get("status"))
    if mapped:
        return mapped
    if node_id in (run_doc.get("outputs") or {}):
        return "done"
    if run_doc.get("status") in _TERMINAL_RUN_STATUSES:
        return "skipped"
    return "pending"


@dataclass
class RunView:
    """The whole run, normalised. Built once per projection."""

    run_id: str
    run_status: str
    workflow_name: str
    started_at: str | None
    ended_at: str | None
    updated_at: str | None
    assigned_to: str | None
    nodes: list[NodeView]
    inputs: dict[str, Any]
    stale_decisions: list[str]
    fact_edits: list[dict[str, Any]]
    route_overrides: list[dict[str, Any]]
    notes: list[dict[str, Any]]
    gate: dict[str, Any] | None
    goal: str
    spec: WorkflowSpec | None

    def __post_init__(self) -> None:
        self._by_id = {node.node_id: node for node in self.nodes}

    def node(self, node_id: str) -> NodeView | None:
        return self._by_id.get(node_id)

    def ran_nodes(self) -> list[NodeView]:
        return [node for node in self.nodes if node.ran]

    def of_kind(self, *kinds: str) -> list[NodeView]:
        return [node for node in self.nodes if node.execution_kind in kinds]

    def of_type(self, *type_names: str) -> list[NodeView]:
        return [node for node in self.nodes if node.type_name in type_names]

    @property
    def is_paused(self) -> bool:
        return bool(self.gate and self.gate.get("paused"))

    @property
    def awaits_approval(self) -> bool:
        return self.is_paused and (self.gate or {}).get("pause_kind") != "user_requested"

    @property
    def is_finished(self) -> bool:
        return self.run_status in _TERMINAL_RUN_STATUSES


def _display_name(node_id: str, spec: NodeSpec | None) -> str:
    experience = spec.experience if spec is not None else None
    return (experience.display_name if experience else None) or humanize_identifier(node_id)


def build_run_view(
    run_doc: dict[str, Any],
    *,
    workflow_spec: WorkflowSpec | None,
    gate: dict[str, Any] | None = None,
    cost_entries: list[dict[str, Any]] | None = None,
) -> RunView:
    """Normalise a run document into the shape the projection reads.

    Works with no workflow spec (a run whose saved YAML no longer parses) by
    falling back to the node ids recorded on the run itself, so Business View
    degrades in labelling rather than failing to open.
    """
    node_runs: dict[str, Any] = run_doc.get("node_runs") or {}
    outputs: dict[str, Any] = run_doc.get("outputs") or {}
    node_types: dict[str, str] = run_doc.get("node_types") or {}

    specs: dict[str, NodeSpec] = {}
    ordered_ids: list[str] = []
    if workflow_spec is not None:
        for node in workflow_spec.nodes:
            specs[node.id] = node
            ordered_ids.append(node.id)
    for node_id in list(node_runs) + list(outputs):
        if node_id not in specs and node_id not in ordered_ids:
            ordered_ids.append(node_id)

    entries_by_node: dict[str, list[dict[str, Any]]] = {}
    for entry in cost_entries or []:
        entries_by_node.setdefault(entry.get("node_id", ""), []).append(entry)

    nodes: list[NodeView] = []
    for node_id in ordered_ids:
        spec = specs.get(node_id)
        record = node_runs.get(node_id) or {}
        type_name = node_types.get(node_id) or record.get("type_name") or (
            spec.type if spec is not None else ""
        )
        nodes.append(
            NodeView(
                node_id=node_id,
                type_name=type_name,
                execution_kind=execution_kind_of(type_name),
                status=_node_status(node_id, run_doc),
                output=outputs.get(node_id, record.get("output")),
                started_at=record.get("started_at"),
                ended_at=record.get("ended_at"),
                error=record.get("error"),
                display_name=_display_name(node_id, spec),
                spec=spec,
                model_selections=list(record.get("model_selections") or []),
                cost_entries=entries_by_node.get(node_id, []),
            )
        )

    goal = None
    if workflow_spec is not None and workflow_spec.experience is not None:
        goal = workflow_spec.experience.goal
    goal = goal or run_doc.get("workflow_name") or "Complete this request."

    return RunView(
        run_id=run_doc.get("run_id", ""),
        run_status=run_doc.get("status", "unknown"),
        workflow_name=run_doc.get("workflow_name", ""),
        started_at=timestamp_iso(run_doc.get("started_at")),
        ended_at=timestamp_iso(run_doc.get("ended_at")),
        updated_at=timestamp_iso(run_doc.get("updated_at")) or timestamp_iso(run_doc.get("started_at")),
        assigned_to=run_doc.get("assigned_to"),
        nodes=nodes,
        inputs=run_doc.get("inputs") or {},
        stale_decisions=list(dict.fromkeys(run_doc.get("stale_decisions") or [])),
        fact_edits=list(run_doc.get("fact_edits") or []),
        route_overrides=list(run_doc.get("route_overrides") or []),
        notes=list(run_doc.get("business_notes") or []),
        gate=gate,
        goal=goal,
        spec=workflow_spec,
    )


def model_usage(nodes: list[NodeView]) -> AIModelUsage | None:
    """Roll up the AI calls made by a set of nodes into one badge (§22–§24).

    `executed` is the model that actually answered — the last one, when a node
    made several calls, because that is the one whose output the user is
    looking at. Latency and cost are summed across the calls; a figure the
    platform never recorded stays None rather than becoming a zero that reads
    as "free and instant".
    """
    selections = [selection for node in nodes for selection in node.model_selections]
    entries = [entry for node in nodes for entry in node.cost_entries]
    if not selections and not entries:
        return None

    requested = selected = executed = None
    fallback = False
    fallback_reason = routing_reason = None
    if selections:
        first, last = selections[0], selections[-1]
        requested = first.get("requested_model")
        selected = last.get("actual_model")
        routing_reason = last.get("reason")
        fallback = any(bool(item.get("fallback")) for item in selections)

    executed_models = [entry.get("model") for entry in entries if entry.get("model")]
    if executed_models:
        executed = executed_models[-1]
    executed = executed or selected
    selected = selected or executed

    for entry in entries:
        if entry.get("fallback_used"):
            fallback = True
            fallback_reason = fallback_reason or entry.get("fallback_reason")
        if requested is None and entry.get("intended_model"):
            requested = entry.get("intended_model")

    latencies = [entry.get("latency_ms") for entry in entries if entry.get("latency_ms") is not None]
    costs = [entry.get("cost_usd") for entry in entries if entry.get("cost_usd") is not None]

    return AIModelUsage(
        requested=requested,
        selected=selected,
        executed=executed,
        fallback=fallback,
        fallback_reason=fallback_reason,
        routing_reason=routing_reason,
        latency_ms=int(sum(latencies)) if latencies else None,
        cost_usd=round(sum(costs), 6) if costs else None,
        task_type=next((entry.get("task_type") for entry in entries if entry.get("task_type")), None),
        provider=next((entry.get("provider") for entry in entries if entry.get("provider")), None),
        call_count=max(len(selections), len(entries)),
    )
