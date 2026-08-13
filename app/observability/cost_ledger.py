from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from app.observability.logging import get_logger
from app.llm.openai_registry import OPENAI_MODEL_REGISTRY

logger = get_logger(__name__)

# Sourced live from OPENAI_MODEL_REGISTRY (app/llm/openai_registry.py) — the
# same catalog the LLM router uses — rather than a hand-copied duplicate.
# A second, separately maintained table is exactly what let this drift
# in the first place: it was missing gpt-5.6-terra/gpt-5.6-luna/gpt-4o-mini/
# o3/o4-mini entirely (silently falling back to the generic default below)
# and had stale numbers for gpt-5/gpt-5-mini. Covers every OpenAI model,
# including embeddings (e.g. text-embedding-3-small), since
# OPENAI_MODEL_REGISTRY prices every kind, not just "llm".
_OPENAI_PRICING: dict[str, tuple[float, float]] = {
    model.name: (model.input_usd_per_1k, model.output_usd_per_1k)
    for model in OPENAI_MODEL_REGISTRY
}

MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5":          (0.005,   0.025),
    "claude-opus-4-8":        (0.005,   0.025),
    "claude-fable-5":         (0.005,   0.025),
    "claude-sonnet-4-5":      (0.003,   0.015),
    "claude-haiku-4-5":       (0.00025, 0.00125),
    # API-metered cost is zero for private endpoints. GPU/infrastructure cost
    # remains an operator metric and must not be presented as free compute.
    "local-kimi-k3":          (0.0,     0.0),
    "local-glm-5":            (0.0,     0.0),
    **_OPENAI_PRICING,
}

# Cache-token price multipliers, applied to a model's input price.
# Anthropic: cache_creation_input_tokens and cache_read_input_tokens are
# reported separately from input_tokens (the uncached remainder) -- see
# shared/prompt-caching.md. Write premium is TTL-dependent; we always use
# the 1h rate here since settings.anthropic_prompt_cache_ttl defaults to
# "1h" (update this if that default changes).
_ANTHROPIC_CACHE_WRITE_MULTIPLIER = 2.0
_ANTHROPIC_CACHE_READ_MULTIPLIER = 0.1
# OpenAI's automatic prompt caching only ever produces read hits (no
# separate write cost) and bills them at half the standard input price.
_OPENAI_CACHE_READ_MULTIPLIER = 0.5


#: `LedgerEntry.cost_source` values. `provider_reported` is a real, authoritative per-call
#: figure the provider returned directly (OpenRouter's `usage.cost` — covers all ~500 of its
#: models, none of which are in Eurskem's own MODEL_PRICING table). `estimated` is computed
#: from MODEL_PRICING, the only option for providers that don't report cost (OpenAI,
#: Anthropic). Both are written synchronously at call time — no reconciliation pass needed,
#: since (unlike the retired OmniRoute-audit design) the authoritative figure is already in
#: the same response the gateway just received.
CostSource = Literal["estimated", "provider_reported"]


@dataclass
class LedgerEntry:
    run_id:         str
    session_id:     str
    node_id:        str
    model:          str           # resolved model (what was actually called)
    intended_model: str           # what the YAML asked for (may differ via fallback)
    input_tokens:   int
    output_tokens:  int
    cost_usd:       float
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens:     int = 0
    cost_source: CostSource = "estimated"
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CostLedger:
    def __init__(self, db: Any):
        self._col = db["cost_ledger"] if db is not None else None

    @staticmethod
    def calculate(
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> float:
        p_in, p_out = MODEL_PRICING.get(model, (0.005, 0.015))
        cost = input_tokens * p_in + output_tokens * p_out
        if model.startswith("claude"):
            cost += cache_creation_input_tokens * p_in * _ANTHROPIC_CACHE_WRITE_MULTIPLIER
            cost += cache_read_input_tokens * p_in * _ANTHROPIC_CACHE_READ_MULTIPLIER
        else:
            # Non-Anthropic models never report cache_creation (no write
            # concept); cache_read here is OpenAI's automatic-cache hit count.
            cost += cache_read_input_tokens * p_in * _OPENAI_CACHE_READ_MULTIPLIER
        return round(cost / 1000, 6)

    def record(self, entry: LedgerEntry) -> None:
        if self._col is not None:
            self._col.insert_one({
                "run_id":         entry.run_id,
                "session_id":     entry.session_id,
                "node_id":        entry.node_id,
                "model":          entry.model,
                "intended_model": entry.intended_model,
                "input_tokens":   entry.input_tokens,
                "output_tokens":  entry.output_tokens,
                "cache_creation_input_tokens": entry.cache_creation_input_tokens,
                "cache_read_input_tokens":     entry.cache_read_input_tokens,
                "cost_usd":       entry.cost_usd,
                "cost_source":    entry.cost_source,
                "ts":             entry.ts,
            })
        logger.info(
            "llm_cost",
            run_id=entry.run_id,
            node_id=entry.node_id,
            model=entry.model,
            intended_model=entry.intended_model,
            cost_usd=entry.cost_usd,
            cache_creation_input_tokens=entry.cache_creation_input_tokens,
            cache_read_input_tokens=entry.cache_read_input_tokens,
            cost_source=entry.cost_source,
        )

    def run_summary(self, run_id: str, session_id: str | None = None) -> dict:
        if self._col is None:
            return {"run_id": run_id, "total_usd": 0.0, "by_node": []}
        query: dict = {"run_id": run_id}
        if session_id is not None:
            query["session_id"] = session_id
        entries = list(self._col.find(query, {"_id": 0}))
        total = round(sum(e["cost_usd"] for e in entries), 6)
        return {"run_id": run_id, "total_usd": total, "by_node": entries}

    def session_summary(self, session_id: str) -> dict:
        if self._col is None:
            return {"session_id": session_id, "total_usd": 0.0}
        entries = list(self._col.find({"session_id": session_id}, {"_id": 0}))
        total = round(sum(e["cost_usd"] for e in entries), 6)
        return {"session_id": session_id, "total_usd": total, "by_run": entries}
