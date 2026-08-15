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

The same idea, at a stricter precision bar, also sizes the request:
`select_generation_model` picks one of three OpenRouter-routed model tiers by
how many genuinely distinct specialized capabilities (not just shortlist
noise) the request touches — see that function's own docstring.
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
    # Filler that happens to appear in a huge fraction of node descriptions
    # ("summarize it in one paragraph", "call one tool", "each request") —
    # without these, an unrelated type can out-score a genuinely relevant
    # one purely on sentence glue rather than shared topic.
    "one", "any", "each", "some", "all", "your", "you", "our", "we",
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


# Two OpenRouter-routed tiers for the generation call itself (see
# app/api/workflow_generation.py's GENERATION_MODEL) — auto-selected by
# request complexity rather than fixed to one model, so a simple routing
# workflow isn't paying complex-tier latency/cost, and a genuinely complex
# one gets the model likely to need fewer repair rounds. Both routed through
# OpenRouter (never a direct provider gateway — that's what went down with
# one account's credit balance); both verified reachable through this
# deployment's OpenRouter key.
GENERATION_MODEL_SIMPLE = "openrouter/openai/gpt-5.6-terra"
GENERATION_MODEL_COMPLEX = "openrouter/openai/gpt-5.6-sol"
# The complex tier's own reasoning-effort dial (gpt-5.6-sol supports none/low/
# medium/high/xhigh) — a complex, multi-capability workflow gets the model's
# most deliberate reasoning, not just its largest weights.
GENERATION_MODEL_COMPLEX_REASONING_EFFORT = "high"

# Categories whose node types carry this platform's heaviest structured
# reasoning (deep evidence graphs, multi-gate submission logic, independent
# cross-model evaluation) — a request touching any of these warrants the
# complex tier even with just one genuine (not incidental) match.
_HEAVY_CATEGORIES = frozenset({"Proposal Engineering", "Evidence & Retrieval"})

# A single shared word is often just sentence glue re-appearing in a type's
# own description ("extract" in three unrelated node names) — two or more
# independently-shared content words is a much stronger relevance signal.
# Deliberately stricter than select_candidate_node_types' own bar (score > 0):
# that shortlist is meant to be generously inclusive (a missed type costs a
# retry; an extra one costs a few free tokens describing it), but a
# complexity estimate built on the same generous bar flags nearly every
# request as complex.
_COMPLEXITY_MIN_SCORE = 2

# Above this many genuinely-relevant specialized types, the workflow is doing
# enough distinct things at once to warrant the complex tier even without a
# "heavy" category present.
_COMPLEX_SPECIALIZED_COUNT = 3


def select_generation_model(prompt: str, manifest: list[dict[str, Any]]) -> str:
    """Deterministic complexity → one of two model tiers, no LLM call.
    Re-scores the request against the registry with a stricter relevance bar
    than `select_candidate_node_types` (see _COMPLEXITY_MIN_SCORE) — sizing
    the task needs precision the shortlist deliberately doesn't have, since
    the shortlist would rather over-include a type than miss one."""
    request_tokens = _tokens(prompt)
    categories: set[str] = set()
    specialized_count = 0
    for entry in manifest:
        type_name = entry.get("type_name")
        if not type_name or entry.get("family") == "core" or type_name in _ALWAYS_INCLUDE_EXTRA:
            continue
        score = len(request_tokens & _tokens(_entry_text(entry)))
        if score >= _COMPLEXITY_MIN_SCORE:
            specialized_count += 1
            categories.add(entry.get("category", "Other"))

    if categories & _HEAVY_CATEGORIES or specialized_count >= _COMPLEX_SPECIALIZED_COUNT:
        return GENERATION_MODEL_COMPLEX
    return GENERATION_MODEL_SIMPLE
