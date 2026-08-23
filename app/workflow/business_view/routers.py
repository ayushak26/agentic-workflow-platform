"""Reading a deterministic router as a business finding.

A `RouterAgent` records everything needed to explain itself — the branch it
took, the condition that matched, and whether it fell through to the default.
What it does *not* record is the business subject of the question it answered:
`safety_router` taking route `CONTINUE` is not a sentence anybody wants to
read, even though `has_safety_issue = false` is exactly what a salesperson
needs to know.

This module recovers that subject from the router's own configuration. Every
condition a router can test names a path (`understand_message.parsed.
has_safety_issue`); when all of a router's conditions test the *same* path,
that path is the router's subject, and its actual value in the run is the
finding to display. Nothing is inferred from node names, and a router whose
conditions disagree about their subject simply reports its route instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.runtime.rules import resolve_path
from app.workflow.business_view.common import humanize_route
from app.workflow.business_view.runstate import NodeView, RunView

ROUTER_TYPES = {"RouterAgent"}
DECISION_TYPES = {"DecisionAgent"}

#: `path OP literal` — the legacy rule grammar RouterAgent._eval_condition
#: parses. Only the left-hand path is needed here.
_RULE_CONDITION = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*(==|!=|<=|>=|<|>)\s*(.+?)\s*$")

#: Node-id fragments that mark a router as answering "who owns this?" rather
#: than "what kind of request is this?". Used only to group activities, never
#: to decide a route.
OWNERSHIP_TERMS = re.compile(
    r"(owner|ownership|territory|kam|key_account|queue|assign|customer_match|"
    r"account_router|dispatch)",
    re.I,
)


@dataclass
class RouterFinding:
    """One router's answer, in business terms where the subject is recoverable."""

    node_id: str
    display_name: str
    route: str
    reason: str | None = None
    used_fallback: bool = False
    matched_conditions: list[str] = field(default_factory=list)
    #: The dotted state path every condition on this router tested, when they
    #: agree on one. None when the router asks about several things at once.
    subject_path: str | None = None
    #: The last segment of `subject_path` — the business field name.
    subject_key: str | None = None
    #: The subject's actual value in this run, read back from run outputs.
    subject_value: Any = None
    subject_resolved: bool = False
    #: The node that produced the subject value, for provenance.
    subject_node_id: str | None = None
    is_ownership: bool = False

    @property
    def route_label(self) -> str:
        """The route label."""
        return humanize_route(self.route)


def _condition_paths(config: dict[str, Any]) -> list[str]:
    """Every state path this router's configuration tests, in declared order."""
    paths: list[str] = []

    route_field = config.get("route_field")
    if isinstance(route_field, str) and route_field:
        paths.append(route_field)

    for rule in config.get("rules") or []:
        condition = (rule or {}).get("condition")
        if isinstance(condition, str):
            match = _RULE_CONDITION.match(condition)
            if match:
                paths.append(match.group(1))

    for case in config.get("cases") or []:
        paths.extend(_group_paths((case or {}).get("when")))

    return paths


def _group_paths(group: Any) -> list[str]:
    """Recursively collect `field` paths out of a structured condition group."""
    if not isinstance(group, dict):
        return []
    paths: list[str] = []
    for condition in group.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        if isinstance(condition.get("field"), str):
            paths.append(condition["field"])
        paths.extend(_group_paths(condition))
    paths.extend(_group_paths(group.get("group")))
    return paths


def _common_subject(paths: list[str]) -> str | None:
    """Internal helper for the common subject step.

    Args:
        paths (list[str]): The paths.

    Returns:
        str | None: The subject.
    """
    unique = list(dict.fromkeys(path for path in paths if path))
    return unique[0] if len(unique) == 1 else None


def read_router(node: NodeView, run: RunView) -> RouterFinding | None:
    """Turn one executed router node into a business finding, or None."""
    output = node.output_dict()
    route = output.get("route")
    if not isinstance(route, str) or not route:
        return None

    config = dict((node.spec.config if node.spec is not None else None) or {})
    subject_path = _common_subject(_condition_paths(config))

    finding = RouterFinding(
        node_id=node.node_id,
        display_name=node.display_name,
        route=route,
        reason=output.get("reason"),
        used_fallback=bool(output.get("used_fallback")),
        matched_conditions=[
            str(item) for item in (output.get("matched_conditions") or []) if item
        ],
        subject_path=subject_path,
        is_ownership=bool(OWNERSHIP_TERMS.search(node.node_id)),
    )

    if subject_path:
        finding.subject_key = subject_path.split(".")[-1]
        context = {"node_outputs": _outputs_of(run), "inputs": run.inputs, "variables": {}}
        value = resolve_path(context, subject_path)
        finding.subject_value = value
        # `resolve_path` returns None both for "absent" and for a genuine null.
        # Only a path whose owning node actually produced output can be shown
        # as a fact — otherwise a lookup that never ran would render as a
        # confident "No".
        head = subject_path.split(".")[0]
        head = "" if head in ("outputs", "node_outputs", "inputs", "variables") else head
        producer = run.node(head) if head else None
        finding.subject_node_id = producer.node_id if producer is not None else None
        finding.subject_resolved = value is not None and (
            producer is None or producer.succeeded
        )

    return finding


def _outputs_of(run: RunView) -> dict[str, Any]:
    """Internal helper for the outputs of step.

    Args:
        run (RunView): The run.

    Returns:
        dict[str, Any]: The of.
    """
    return {node.node_id: node.output for node in run.nodes if node.output is not None}


def router_findings(run: RunView) -> list[RouterFinding]:
    """Every router that actually ran, in execution order."""
    findings: list[RouterFinding] = []
    for node in run.nodes:
        if node.type_name not in ROUTER_TYPES or not node.succeeded:
            continue
        finding = read_router(node, run)
        if finding is not None:
            findings.append(finding)
    return findings
