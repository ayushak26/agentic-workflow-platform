"""Claim-evidence verification with an exact-passage safety check.

The model classifies semantic support, but the application accepts a citation
only when the model also returns a verbatim passage that exists in the supplied
source version. This prevents a fluent judge explanation from becoming
evidence when it cannot point to text a reviewer can inspect.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.proposal_graph.models import EvidenceRelation, EvidenceStance


class ClaimSupportVerdict(BaseModel):
    """Pydantic model defining the ClaimSupportVerdict shape.

    Attributes:
        stance (EvidenceStance).
        confidence (float).
        reason (str).
        supporting_quote (str).
    """
    stance: EvidenceStance
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    supporting_quote: str = ""


def _normalise_space(value: str) -> str:
    """Internal helper for the normalise space step.

    Args:
        value (str): Value to process.

    Returns:
        str: The space.
    """
    return re.sub(r"\s+", " ", value).strip()


def quote_exists_in_source(quote: str, source_text: str) -> bool:
    """Whitespace-tolerant, otherwise verbatim passage validation."""

    normal_quote = _normalise_space(quote)
    return bool(normal_quote) and normal_quote in _normalise_space(source_text)


async def verify_claim_against_text(
    llm: Any,
    *,
    claim: str,
    source_text: str,
    model: str = "claude-sonnet-4-5",
) -> ClaimSupportVerdict:
    """Return a typed verdict and fail closed when the quote is not traceable."""

    if not claim.strip():
        raise ValueError("claim cannot be empty")
    if not source_text.strip():
        return ClaimSupportVerdict(
            stance=EvidenceStance.INSUFFICIENT,
            confidence=0.0,
            reason="The source text is empty.",
            supporting_quote="",
        )

    verdict = await llm.complete_structured(
        model=model,
        system=(
            "You verify one factual claim against one source passage. "
            "Classify it as supports, contradicts, or insufficient. Return a "
            "short VERBATIM quote copied from SOURCE when supports or "
            "contradicts. Never invent or paraphrase the quote. Confidence "
            "measures the strength of the relation, not writing quality."
        ),
        user=(
            f"CLAIM:\n{claim}\n\nSOURCE:\n{source_text}\n\n"
            "Decide whether this source version supports or contradicts the "
            "claim. If the relationship is only contextual or ambiguous, use "
            "insufficient."
        ),
        response_model=ClaimSupportVerdict,
        temperature=0.0,
        max_tokens=700,
    )

    if verdict.stance != EvidenceStance.INSUFFICIENT and not quote_exists_in_source(
        verdict.supporting_quote,
        source_text,
    ):
        return ClaimSupportVerdict(
            stance=EvidenceStance.INSUFFICIENT,
            confidence=0.0,
            reason=(
                "The semantic judge returned a quote that is not present in "
                "the supplied source version; verification failed closed."
            ),
            supporting_quote="",
        )
    return verdict


def relation_from_verdict(
    *,
    relation_id: str,
    claim_id: str,
    source_id: str,
    source_version_id: str | None,
    locator: str,
    verifier_model: str,
    verdict: ClaimSupportVerdict,
) -> EvidenceRelation:
    """Compute the relation from verdict.

    Args:
        relation_id (str): The relation id.
        claim_id (str): The claim id.
        source_id (str): The source id.
        source_version_id (str | None): The source version id.
        locator (str): The locator.
        verifier_model (str): The verifier model.
        verdict (ClaimSupportVerdict): The verdict.

    Returns:
        EvidenceRelation: The from verdict.
    """
    passage = verdict.supporting_quote.strip()
    return EvidenceRelation(
        id=relation_id,
        claim_id=claim_id,
        source_id=source_id,
        source_version_id=source_version_id,
        passage=passage,
        locator=locator,
        passage_sha256=hashlib.sha256(passage.encode("utf-8")).hexdigest(),
        stance=verdict.stance,
        confidence=verdict.confidence,
        reason=verdict.reason,
        verifier_model=verifier_model,
        verified_at=datetime.now(timezone.utc).isoformat(),
    )
