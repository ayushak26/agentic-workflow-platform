"""BusinessStatusNarrator — turning validated state into concise business words.

What it is allowed to do: rephrase a status that has *already been decided*
deterministically, so the headline reads like a colleague wrote it rather than
like a state machine emitted it.

What it is not allowed to do, ever: change state, choose a route, authorise an
action, or introduce a fact. Those constraints are enforced here rather than
requested in a prompt — the narration is discarded unless every entity and
number in it appears in the bounded input it was given (§15, §62).

The screen never depends on it. `deterministic_narration` produces the exact
same shape from templates, and it is what renders when the model is
unavailable, slow, or wrong (§16).

Cost control (§17, §50): one small call per *meaningful* state change, keyed on
`business_status.state_version`, which deliberately ignores timings and costs.
A re-render, a poll, or a mouse click never spends a token.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.observability.logging import get_logger
from app.workflow.business_view.models import BusinessProjection

logger = get_logger(__name__)

#: Presentation calls declare their own capability so the platform's
#: evaluation-driven routing can pick something cheap and fast; narration must
#: never reach for the strongest reasoning model (§51).
NARRATION_CAPABILITY = "business_status_narration"

#: A deliberately small, fast model. Presentation copy is a formatting task,
#: not a reasoning one.
NARRATION_MODEL = "gpt-5.6-luna"

#: Hard bounds — a status line that needs more than this is not a status line.
MAX_TOKENS = 220
TEMPERATURE = 0.1

_SYSTEM_PROMPT = (
    "You rewrite an already-decided business status into plain, calm business "
    "English for a salesperson.\n"
    "You must not decide anything, recommend anything, or add any fact, name, "
    "number, product, date or company that is not present in the INPUT.\n"
    "Do not mention AI, models, workflows, nodes, routers or JSON.\n"
    "headline: at most 8 words, no full stop.\n"
    "summary: one sentence, at most 30 words.\n"
    "next_step: one sentence describing what the named owner does next, or an "
    "empty string if the input does not say."
)


class NarrationInput(BaseModel):
    """Everything the narrator is allowed to see. Nothing else is sent."""

    current_activity: str | None = None
    business_status: str
    business_decision: str | None = None
    important_facts: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    pending_user_actions: list[str] = Field(default_factory=list)
    completed_actions: list[str] = Field(default_factory=list)


class Narration(BaseModel):
    """Pydantic model defining the Narration shape.

    Attributes:
        headline (str).
        summary (str).
        next_step (str).
    """
    headline: str
    summary: str
    next_step: str = ""


def narration_input(projection: BusinessProjection) -> NarrationInput:
    """The bounded, structured input contract (§14)."""
    active = next((a for a in projection.activities if a.status == "active"), None)
    return NarrationInput(
        current_activity=active.title if active else None,
        business_status=projection.business_status.headline,
        business_decision=projection.decision.headline if projection.decision else None,
        important_facts=[
            f"{fact.label}: {fact.display}"
            for fact in projection.understanding.fields[:8]
            if not fact.missing
        ]
        + [f"Customer: {projection.work_item.customer}" for _ in (1,) if projection.work_item.customer],
        missing_information=[item.title for item in projection.attention[:6]],
        pending_user_actions=[
            action.question or "A review is pending"
            for action in projection.required_user_actions
        ],
        completed_actions=[activity.title for activity in projection.activities if activity.status == "completed"],
    )


def deterministic_narration(projection: BusinessProjection) -> Narration:
    """The narration the screen uses when no model is involved (§16).

    This is not a degraded mode — it is the baseline the model is only ever
    allowed to improve on.
    """
    status = projection.business_status
    return Narration(
        headline=status.headline,
        summary=status.summary,
        next_step=(projection.next_step.description or projection.next_step.headline)
        if projection.next_step
        else "",
    )


#: Tokens that carry a claim: capitalised words (names, teams, products) and
#: anything containing a digit (quantities, references, dates).
_CLAIM_TOKEN = re.compile(r"\b([A-Z][\w&./-]{1,}|\w*\d[\w./-]*)\b")

#: Words that are capitalised because they start a sentence or are ordinary
#: business vocabulary, not because they name something.
_SAFE_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "before", "by", "can", "for", "from",
    "has", "have", "in", "is", "it", "its", "needs", "no", "not", "of", "on", "once",
    "or", "ready", "requires", "review", "reviews", "so", "some", "than", "that", "the",
    "then", "there", "they", "this", "to", "until", "waiting", "was", "we", "when",
    "which", "while", "will", "with", "you", "your", "customer", "request", "team",
    "information", "missing", "next", "step", "work", "item", "prepares", "prepare",
    "response", "quotation", "order", "details", "attention", "completed", "paused",
    "stopped", "approval", "nothing", "outstanding", "progress", "check", "checks",
}


def _claims(text: str) -> set[str]:
    """Internal helper for the claims step.

    Args:
        text (str): The text.

    Returns:
        set[str]: The result.
    """
    return {
        token.lower()
        for token in _CLAIM_TOKEN.findall(text or "")
        if token.lower() not in _SAFE_WORDS
    }


def validate(narration: Narration, source: NarrationInput) -> str | None:
    """None when the narration is usable, otherwise why it was rejected.

    The test is containment: every name, reference and number in the output
    must already appear in the input. That is a blunt instrument, and
    deliberately so — a narrator that invents a delivery date is far worse than
    one whose output is occasionally discarded in favour of the deterministic
    template.
    """
    if not narration.headline.strip() or not narration.summary.strip():
        return "empty"
    if len(narration.headline.split()) > 12:
        return "headline too long"
    if len(narration.summary.split()) > 45:
        return "summary too long"

    allowed = _claims(
        " ".join(
            [
                source.business_status,
                source.business_decision or "",
                source.current_activity or "",
                *source.important_facts,
                *source.missing_information,
                *source.pending_user_actions,
                *source.completed_actions,
            ]
        )
    )
    produced = _claims(f"{narration.headline} {narration.summary} {narration.next_step}")
    unsupported = sorted(produced - allowed)
    if unsupported:
        return f"unsupported: {', '.join(unsupported[:5])}"
    return None


async def narrate(
    llm: Any,
    projection: BusinessProjection,
    *,
    model: str = NARRATION_MODEL,
) -> tuple[Narration, str, str | None]:
    """Return (narration, source, model) — falling back rather than failing.

    `source` is "ai" only when a model produced text that passed validation;
    every other path returns the deterministic narration, so a caller never has
    to handle an error to render a screen.
    """
    fallback = deterministic_narration(projection)
    if llm is None:
        return fallback, "deterministic", None

    source = narration_input(projection)
    try:
        result = await llm.complete_structured(
            model=model,
            system=_SYSTEM_PROMPT,
            user=f"INPUT:\n{source.model_dump_json(indent=2)}",
            response_model=Narration,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
    except Exception as exc:  # narration must never break the screen
        logger.warning("business_narration_failed", error=str(exc), run_id=projection.work_item.id)
        return fallback, "deterministic", None

    rejection = validate(result, source)
    if rejection is not None:
        logger.info(
            "business_narration_rejected",
            reason=rejection,
            run_id=projection.work_item.id,
        )
        return fallback, "deterministic", None

    executed = getattr(result, "_model", None) or model
    return result, "ai", executed


def apply(projection: BusinessProjection, narration: Narration, *, source: str, model: str | None) -> None:
    """Fold a narration into the projection's status, in place.

    Only wording moves: `code`, `tone` and `attention_count` are untouched, so
    nothing downstream can be steered by what the narrator said (§15).
    """
    projection.business_status.headline = narration.headline
    projection.business_status.summary = narration.summary
    projection.business_status.narration_source = "ai" if source == "ai" else "deterministic"
    projection.business_status.narration_model = model
    if narration.next_step and projection.next_step is not None:
        projection.next_step.description = narration.next_step
