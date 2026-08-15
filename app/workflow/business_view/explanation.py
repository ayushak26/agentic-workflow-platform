"""«Why?» — the facts and rules behind a decision, optionally in plainer words.

The deterministic explanation is the product: a list of the facts that were
true and the rules that fired, both taken verbatim from the run. That is
already complete and always correct, and it is what renders by default (§20).

A small model may rewrite those into one readable paragraph. It is given the
facts and rules with ids, and must cite the ids it used. Any explanation
referencing an id that was not supplied is discarded whole — not patched, not
partially shown — and the deterministic form is used instead (§49, §63).

Chain-of-thought is never requested and never displayed. The output is a
summary of evidence, not a reconstruction of reasoning.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.observability.logging import get_logger
from app.workflow.business_view.models import BusinessDecisionView

logger = get_logger(__name__)

EXPLANATION_CAPABILITY = "business_explanation"
EXPLANATION_MODEL = "gpt-5.6-luna"
MAX_TOKENS = 320
TEMPERATURE = 0.1

_SYSTEM_PROMPT = (
    "You explain, in one short business paragraph, why a customer request was "
    "handled the way it was.\n"
    "Use ONLY the supplied FACTS and RULES. Do not add reasons, conditions, "
    "names or numbers that are not in them, and do not describe your reasoning "
    "process.\n"
    "Cite the fact ids and rule ids you used in fact_refs and rule_refs.\n"
    "At most 60 words."
)


class DecisionExplanation(BaseModel):
    summary: str
    fact_refs: list[str] = Field(default_factory=list)
    rule_refs: list[str] = Field(default_factory=list)


class ExplanationView(BaseModel):
    """What the Why? panel renders."""

    decision: str
    summary: str | None = None
    facts: list[dict[str, str]] = Field(default_factory=list)
    rules: list[dict[str, str]] = Field(default_factory=list)
    source: str = "deterministic"
    model: str | None = None


def deterministic_explanation(decision: BusinessDecisionView) -> ExplanationView:
    """Facts and rules, exactly as the run recorded them."""
    return ExplanationView(
        decision=decision.headline,
        summary=decision.reason,
        facts=[
            {"id": fact.id, "label": fact.label, "value": fact.display, "source": fact.source_label}
            for fact in decision.facts
        ],
        rules=[
            {"id": rule.id, "name": rule.name, "description": rule.description or ""}
            for rule in decision.rules
            if rule.matched
        ]
        or [
            {"id": rule.id, "name": rule.name, "description": rule.description or ""}
            for rule in decision.rules
        ],
        source="deterministic",
    )


def validate(
    explanation: DecisionExplanation, view: ExplanationView,
) -> str | None:
    """None when every citation resolves, otherwise why it was rejected."""
    if not explanation.summary.strip():
        return "empty"
    if len(explanation.summary.split()) > 90:
        return "too long"
    known_facts = {item["id"] for item in view.facts}
    known_rules = {item["id"] for item in view.rules}
    unknown = [ref for ref in explanation.fact_refs if ref not in known_facts]
    unknown += [ref for ref in explanation.rule_refs if ref not in known_rules]
    if unknown:
        return f"unknown references: {', '.join(unknown[:5])}"
    if not explanation.fact_refs and not explanation.rule_refs:
        # An explanation grounded in nothing is indistinguishable from one made
        # up, so it is treated as made up.
        return "no supporting evidence cited"
    return None


async def explain(
    llm: Any,
    decision: BusinessDecisionView,
    *,
    model: str = EXPLANATION_MODEL,
) -> ExplanationView:
    """The Why? panel — plainer wording when it validates, evidence always."""
    view = deterministic_explanation(decision)
    if llm is None or (not view.facts and not view.rules):
        return view

    payload = {
        "decision": decision.headline,
        "facts": view.facts,
        "rules": view.rules,
    }
    try:
        result = await llm.complete_structured(
            model=model,
            system=_SYSTEM_PROMPT,
            user=str(payload),
            response_model=DecisionExplanation,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
    except Exception as exc:
        logger.warning("business_explanation_failed", error=str(exc))
        return view

    rejection = validate(result, view)
    if rejection is not None:
        logger.info("business_explanation_rejected", reason=rejection)
        return view

    view.summary = result.summary
    view.source = "ai"
    view.model = model
    return view
