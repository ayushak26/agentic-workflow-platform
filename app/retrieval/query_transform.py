"""Rewrite, multi-query, decomposition, HyDE and validated self-query.

Every strategy degrades to a no-op on any LLM/parsing failure — a broken
transform must never take retrieval down with it. ``self_query`` is the one
strategy that can propose metadata filters; those are validated against the
Collection's metadata_schema (and reserved security fields are dropped)
before they are ever ANDed onto the caller's own filters.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.retrieval.filters import RESERVED_METADATA_FIELDS, validate_metadata_filters
from app.retrieval.models import MetadataFilterGroup, MetadataFilterPredicate, RetrievalQuery

_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*\d]+[.)]?)\s*", re.MULTILINE)


async def _safe_complete(llm: Any, *, system: str, user: str) -> str | None:
    """Internal helper for the safe complete step.

    Args:
        llm (Any): The llm.
        system (str): The system.
        user (str): Authenticated current user.

    Returns:
        str | None: The complete.
    """
    if llm is None:
        return None
    try:
        response = await llm.complete(model="auto", system=system, user=user, temperature=0.0)
        return response.text.strip()
    except Exception:
        return None


def _split_lines(text: str, *, limit: int) -> list[str]:
    """Split the lines.

    Args:
        text (str): The text.
        limit (int): Maximum number of items to return.

    Returns:
        list[str]: The lines.
    """
    lines = [_LIST_ITEM_RE.sub("", line).strip() for line in text.splitlines()]
    return [line for line in lines if line][:limit]


async def transform_query(
    request: RetrievalQuery, *, llm: Any, metadata_schema: dict[str, Any]
) -> tuple[str, list[str], MetadataFilterGroup | None]:
    """Return ``(semantic_query, transformed_queries, generated_filters)``.

    ``semantic_query`` is what reranking/compression score relevance against.
    ``transformed_queries`` is what actually gets executed against the
    datastore — usually one query, sometimes several fanned out and fused
    with Reciprocal Rank Fusion by the caller.
    """

    query = request.query
    mode = request.query_transform

    if mode == "none":
        return query, [query], None

    if mode == "rewrite":
        rewritten = await _safe_complete(
            llm,
            system="Rewrite the question into one precise, keyword-rich search query. Reply with only the rewritten query.",
            user=query,
        )
        rewritten = rewritten or query
        return rewritten, [rewritten], None

    if mode == "multi_query":
        text = await _safe_complete(
            llm,
            system=(
                "Generate 3 different search queries that would each surface relevant "
                "results for this question. One per line, no numbering, no commentary."
            ),
            user=query,
        )
        variants = _split_lines(text, limit=3) if text else []
        transformed = [query, *[v for v in variants if v != query]]
        return query, transformed or [query], None

    if mode == "decomposition":
        text = await _safe_complete(
            llm,
            system=(
                "Break this question into up to 4 simpler sub-questions whose answers "
                "together answer it. One per line, no numbering, no commentary."
            ),
            user=query,
        )
        parts = _split_lines(text, limit=4) if text else []
        return query, parts or [query], None

    if mode == "hyde":
        hypothetical = await _safe_complete(
            llm,
            system=(
                "Write a short, plausible passage that would directly answer this "
                "question, as if it were an excerpt from a real document. Reply with "
                "only the passage."
            ),
            user=query,
        )
        return query, [hypothetical or query], None

    if mode == "self_query":
        raw = await _safe_complete(
            llm,
            system=(
                "Extract a cleaned search query and any explicit metadata filters from "
                "this question. Reply with ONLY compact JSON: "
                '{"query": "...", "filters": {"field": "value"}}. Omit filters you are '
                "not confident about; use an empty object if none apply."
            ),
            user=query,
        )
        cleaned_query, generated = query, None
        if raw:
            try:
                payload = json.loads(raw)
                cleaned_query = str(payload.get("query") or query).strip() or query
                raw_filters = payload.get("filters") or {}
                predicates = [
                    MetadataFilterPredicate(field=str(field), operator="equals", value=value)
                    for field, value in raw_filters.items()
                    if str(field) not in RESERVED_METADATA_FIELDS
                ]
                if predicates:
                    candidate = MetadataFilterGroup(logic="and", predicates=predicates)
                    # An LLM-proposed filter is never trusted past schema
                    # validation — an invalid field is dropped, not raised,
                    # since a malformed self-query must degrade, not fail.
                    try:
                        validate_metadata_filters(candidate, metadata_schema)
                        generated = candidate
                    except ValueError:
                        generated = None
            except (json.JSONDecodeError, TypeError, ValueError):
                cleaned_query, generated = query, None
        return cleaned_query, [cleaned_query], generated

    return query, [query], None
