"""Editing an extracted fact on a run, and marking what it fed as stale.

Two sources of truth for "which decisions depend on this field", in order:

* **Derived from the workflow spec** (`derive_dependencies`). Every
  `RouterAgent`/`DecisionAgent` condition names the state path it reads, so a
  workflow whose branching is expressed in routers tells us its own field →
  decision graph. Nothing to maintain, and it cannot drift.
* **FACT_DEPENDENCIES**, a hand transcription for
  workflows/crm_aware_customer_triage.yaml, whose `assess_request`
  DecisionAgent sets named decision *fields* rather than routes. Kept because
  those field names ("complexity", "human_review") are what the Business View
  marks stale, and no spec inspection recovers that mapping.

Editing a fact does NOT recompute anything — it marks the dependents stale so
a person knows a decision was made from a value that has since changed.
Recomputation is the existing retry/restart control, not a second engine here.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

#: The node whose structured extraction these fields live on.
NODE_ID = "understand_request"

#: extraction field -> decision keys derived from it (see the rule name in
#: parentheses, matching workflows/crm_aware_customer_triage.yaml).
FACT_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    # "A ... support need drives routing" / "primary_intent" rules
    "request_types": ("primary_intent", "complexity", "human_review"),
    # "An ATEX or hazardous-area requirement needs a specialist"
    "hazardous_area": ("complexity", "human_review", "escalation_reason"),
    # "A custom design needs a specialist"
    "requires_custom_design": ("complexity", "human_review", "escalation_reason"),
    # "Several simultaneous operating parameters indicate a complex case"
    "temperature": ("complexity", "human_review", "escalation_reason"),
    "pressure": ("complexity", "human_review", "escalation_reason"),
    "viscosity": ("complexity", "human_review", "escalation_reason"),
    # "A vague product reference ..." / "A spare part request with no model ..."
    "refers_to_previous_purchase": ("product_resolved", "human_review", "escalation_reason"),
    "product_model": ("product_resolved", "human_review", "escalation_reason"),
    # "Stopped production is critical"
    "production_stopped": ("urgency",),
}

EDITABLE_FIELDS = frozenset(FACT_DEPENDENCIES)

#: Where an extraction node keeps its structured fields. `parsed` is
#: TransformAgent's contract; `result` is the older extraction shape.
_PAYLOAD_KEYS = ("parsed", "result")

#: `path OP literal` — the RouterAgent legacy rule grammar. Only the path matters.
_CONDITION = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*(?:==|!=|<=|>=|<|>)\s*")


def stale_decisions_for(field: str) -> tuple[str, ...]:
    return FACT_DEPENDENCIES.get(field, ())


def _condition_paths(config: dict[str, Any]) -> list[str]:
    """Every state path a router/decision node's configuration reads."""
    paths: list[str] = []
    route_field = config.get("route_field")
    if isinstance(route_field, str):
        paths.append(route_field)
    for rule in config.get("rules") or []:
        condition = (rule or {}).get("condition")
        if isinstance(condition, str):
            match = _CONDITION.match(condition)
            if match:
                paths.append(match.group(1))
        paths.extend(_group_paths((rule or {}).get("when")))
    for case in config.get("cases") or []:
        paths.extend(_group_paths((case or {}).get("when")))
    return paths


def _group_paths(group: Any) -> list[str]:
    if not isinstance(group, dict):
        return []
    paths: list[str] = []
    for condition in group.get("conditions") or []:
        if isinstance(condition, dict):
            if isinstance(condition.get("field"), str):
                paths.append(condition["field"])
            paths.extend(_group_paths(condition))
    paths.extend(_group_paths(group.get("group")))
    return paths


def _field_of(path: str, node_id: str) -> str | None:
    """`understand_message.parsed.pump_model` → `pump_model`, for this node only."""
    parts = [part for part in path.split(".") if part]
    if parts and parts[0] in ("outputs", "node_outputs"):
        parts = parts[1:]
    if len(parts) < 2 or parts[0] != node_id:
        return None
    if parts[1] in _PAYLOAD_KEYS:
        parts = parts[1:]
        return parts[1] if len(parts) > 1 else None
    return parts[1]


def derive_dependencies(spec: Any, node_id: str) -> dict[str, tuple[str, ...]]:
    """extraction field → the decision nodes that read it, from the spec itself.

    Returns node ids (routers are the decisions in these workflows), so the
    Business View can say *which* determination may no longer hold after an
    edit. Returns `{}` for a spec that is missing or expresses branching some
    other way, which simply means no staleness can be claimed.
    """
    dependencies: dict[str, set[str]] = {}
    for node in getattr(spec, "nodes", None) or []:
        if getattr(node, "type", None) not in ("RouterAgent", "DecisionAgent"):
            continue
        for path in _condition_paths(dict(getattr(node, "config", None) or {})):
            field = _field_of(path, node_id)
            if field:
                dependencies.setdefault(field, set()).add(node.id)
    return {field: tuple(sorted(nodes)) for field, nodes in dependencies.items()}


def payload_key_for(output: Any) -> str:
    """Which sub-object of an extraction node's output holds its fields."""
    if isinstance(output, dict):
        for key in _PAYLOAD_KEYS:
            if isinstance(output.get(key), dict):
                return key
    return "result"


async def apply_fact_correction(
    db: Any,
    *,
    run_id: str,
    session_id: str,
    field: str,
    value: Any,
    node_id: str | None = None,
    payload_key: str = "result",
    stale_decisions: tuple[str, ...] | list[str] | None = None,
    allowed_fields: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    """Overwrite one extracted field on a run and mark what it fed as stale.

    `node_id`/`payload_key`/`stale_decisions`/`allowed_fields` default to the
    hand-mapped crm_aware_customer_triage behaviour, so existing callers are
    unchanged; the Business View passes the values it derived from the run's
    own workflow spec.

    Raises ValueError for a field this run does not expose for editing, and
    LookupError if the run doesn't belong to this session.
    """
    target_node = node_id or NODE_ID
    permitted = allowed_fields if allowed_fields is not None else EDITABLE_FIELDS
    if field not in permitted:
        raise ValueError(f"'{field}' is not an editable field on this work item.")

    stale = tuple(stale_decisions) if stale_decisions is not None else stale_decisions_for(field)
    edit_record = {
        "field": field,
        "value": value,
        "node_id": target_node,
        "stale_decisions": list(stale),
        "edited_at": datetime.now(timezone.utc).isoformat(),
    }

    update: dict[str, Any] = {
        "$set": {
            f"outputs.{target_node}.{payload_key}.{field}": value,
            "updated_at": datetime.now(timezone.utc),
        },
        "$push": {"fact_edits": edit_record},
    }
    if stale:
        update["$addToSet"] = {"stale_decisions": {"$each": list(stale)}}

    result = await db["run_history"].update_one(
        {"run_id": run_id, "session_id": session_id}, update,
    )
    if result.matched_count == 0:
        raise LookupError("Run not found")
    return edit_record
