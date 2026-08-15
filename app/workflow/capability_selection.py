"""Deterministic, zero-token capability → node-type shortlisting for workflow
generation (see app/api/workflow_generation.py).

Sending every registered node type's full schema to the model on every
generation request wastes tokens on types the request has nothing to do with,
and dilutes the types that actually matter. This module picks a shortlist
instead:

    every "core" node type (the reusable glue vocabulary — always included,
    reused from the registry's own family classification, not re-listed here)
        +
    Literal / Echo (trivial, near-zero schema cost, and the worked example in
    the generation system prompt is built from them)
        +
    whatever specialized types share vocabulary with the request

No LLM call, no embeddings — plain keyword overlap against text the registry
already exposes (type name, description, category, about). This is
intentionally coarse: it only has to narrow ~49 types down to a shortlist the
model can specialize the ordinary way, not pick the exactly-right type on its
own.
"""
from __future__ import annotations

import re
from typing import Any

# Deliberately small and generic — not tuned per node type. Filtering these
# out keeps the scoring signal on domain words instead of sentence glue.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "has", "have", "if", "in", "into", "is", "it", "its",
    "of", "on", "or", "so", "that", "the", "this", "to", "up", "was",
    "were", "what", "when", "which", "with", "workflow", "workflows",
    "step", "node", "using", "use", "used",
})

# Cheap even when included on every request; the worked example the model is
# shown is built from these two.
_ALWAYS_INCLUDE_EXTRA = frozenset({"Literal", "Echo"})

_WORD_RE = re.compile(r"[a-z]+")


def _split_pascal_case(name: str) -> str:
    """"RouterAgent" -> "router agent"; "MCPToolAgent" -> "mcp tool agent" —
    without this, a type name is one opaque token and never matches a
    request's ordinary-language words."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", name)


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD_RE.findall(text.lower()) if word not in _STOPWORDS and len(word) > 2}


def _entry_text(entry: dict[str, Any]) -> str:
    about = entry.get("about") or {}
    about_text = " ".join(str(value) for value in about.values() if isinstance(value, str))
    return " ".join([
        _split_pascal_case(entry.get("type_name", "")),
        entry.get("description") or "",
        entry.get("category") or "",
        about_text,
    ])


def select_candidate_node_types(
    prompt: str,
    manifest: list[dict[str, Any]],
    *,
    max_candidates: int = 12,
) -> list[str]:
    """Return the type names the generator's system prompt should describe:
    every core node type, Literal/Echo, and up to `max_candidates` specialized
    types ranked by keyword overlap with `prompt`. Deterministic — same
    inputs always produce the same shortlist, in a stable order (manifest
    order for the always-included set, then score-descending for the rest).
    """
    request_tokens = _tokens(prompt)

    always_include: list[str] = []
    scored: list[tuple[int, str]] = []
    for entry in manifest:
        type_name = entry.get("type_name")
        if not type_name:
            continue
        if entry.get("family") == "core" or type_name in _ALWAYS_INCLUDE_EXTRA:
            always_include.append(type_name)
            continue
        score = len(request_tokens & _tokens(_entry_text(entry)))
        if score > 0:
            scored.append((score, type_name))

    scored.sort(key=lambda item: (-item[0], item[1]))
    shortlisted = [name for _, name in scored[:max_candidates]]

    return always_include + shortlisted
