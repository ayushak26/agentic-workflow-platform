"""Editable-fact correction for the flagship customer-triage workflow.

Deliberately a small, hand-written map, not a generic "field dependency
engine" — this platform has one workflow whose rule set is worth wiring this
for today (workflows/crm_aware_customer_triage.yaml), and its rules are
fixed and few. FACT_DEPENDENCIES below is a direct transcription of that
file's `assess_request` rule conditions: every `outputs.understand_request.
result.<field>` a rule reads is a key here, mapped to the decision fields
that rule sets.

Editing a fact here does NOT recompute those decisions — it only marks them
stale, so a person knows a downstream decision was made from a value that
has since changed. Recomputation is the existing "Retry safely" control
Business View/Cockpit already offer; this module does not duplicate it.
"""
from __future__ import annotations

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


def stale_decisions_for(field: str) -> tuple[str, ...]:
    return FACT_DEPENDENCIES.get(field, ())


async def apply_fact_correction(
    db: Any, *, run_id: str, session_id: str, field: str, value: Any,
) -> dict[str, Any]:
    """Overwrite one extracted field on a run and mark what it fed as stale.

    Raises ValueError for a field this map doesn't cover, LookupError if the
    run doesn't belong to this session.
    """
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"'{field}' is not an editable, rule-linked field on this workflow.")

    stale = stale_decisions_for(field)
    edit_record = {
        "field": field,
        "value": value,
        "stale_decisions": list(stale),
        "edited_at": datetime.now(timezone.utc).isoformat(),
    }

    result = await db["run_history"].update_one(
        {"run_id": run_id, "session_id": session_id},
        {
            "$set": {
                f"outputs.{NODE_ID}.result.{field}": value,
                "updated_at": datetime.now(timezone.utc),
            },
            "$push": {"fact_edits": edit_record},
            "$addToSet": {"stale_decisions": {"$each": list(stale)}},
        },
    )
    if result.matched_count == 0:
        raise LookupError("Run not found")
    return edit_record
