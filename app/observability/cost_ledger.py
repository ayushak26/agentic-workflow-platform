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
    # Task/provider/latency telemetry (§30 requires these per call).
    task_type:       str = "unknown"   # e.g. structured_extraction, reasoning, rerank
    provider:        str = "unknown"   # e.g. openrouter, anthropic, openai, moonshot-local
    latency_ms:      float | None = None
    fallback_used:   bool = False
    fallback_reason: str | None = None
    # RAG pipeline stage this call belongs to (query_rewrite/rerank/compress/
    # generation), or None for a call outside a RAG pipeline entirely.
    stage: str | None = None
    # True for an explicit zero-cost telemetry entry (a pipeline stage that
    # genuinely has no model charge, e.g. plain hybrid search) rather than a
    # priced call that happened to cost $0.
    no_model_charge: bool = False
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
                "task_type":      entry.task_type,
                "provider":       entry.provider,
                "latency_ms":     entry.latency_ms,
                "fallback_used":  entry.fallback_used,
                "fallback_reason": entry.fallback_reason,
                "stage":          entry.stage,
                "no_model_charge": entry.no_model_charge,
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
            task_type=entry.task_type,
            provider=entry.provider,
            latency_ms=entry.latency_ms,
            fallback_used=entry.fallback_used,
            fallback_reason=entry.fallback_reason,
            stage=entry.stage,
            no_model_charge=entry.no_model_charge,
        )

    def record_no_charge(
        self,
        *,
        run_id: str,
        session_id: str,
        node_id: str,
        stage: str,
        task_type: str = "retrieval",
        note: str = "",
    ) -> None:
        """Log a pipeline stage that genuinely has no model charge.

        Used so a stage like plain hybrid search shows up in a cost
        breakdown as "no model charge" rather than being silently absent —
        absence reads as "not measured", not as "measured at zero" (§29/§33).
        """
        self.record(
            LedgerEntry(
                run_id=run_id,
                session_id=session_id,
                node_id=node_id,
                model="",
                intended_model="",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                task_type=task_type,
                provider="",
                stage=stage,
                no_model_charge=True,
                fallback_reason=note or None,
            )
        )

    @staticmethod
    def _group_by(entries: list[dict], key: str) -> list[dict]:
        groups: dict[str, dict] = {}
        order: list[str] = []
        for entry in entries:
            label = entry.get(key) or "unknown"
            if label not in groups:
                groups[label] = {
                    key: label,
                    "calls": 0,
                    "cost_usd": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "latency_ms_total": 0.0,
                    "latency_samples": 0,
                    "no_model_charge": True,
                }
                order.append(label)
            group = groups[label]
            group["calls"] += 1
            group["cost_usd"] += entry.get("cost_usd") or 0.0
            group["input_tokens"] += entry.get("input_tokens") or 0
            group["output_tokens"] += entry.get("output_tokens") or 0
            if entry.get("latency_ms") is not None:
                group["latency_ms_total"] += entry["latency_ms"]
                group["latency_samples"] += 1
            if not entry.get("no_model_charge"):
                group["no_model_charge"] = False
        result = []
        for label in order:
            group = groups[label]
            samples = group.pop("latency_samples")
            total_latency = group.pop("latency_ms_total")
            group["cost_usd"] = round(group["cost_usd"], 6)
            group["avg_latency_ms"] = (
                round(total_latency / samples, 1) if samples else None
            )
            result.append(group)
        return result

    def run_summary(self, run_id: str, session_id: str | None = None) -> dict:
        if self._col is None:
            return {
                "run_id": run_id,
                "total_usd": 0.0,
                "by_node": [],
                "by_node_summary": [],
                "by_task_type": [],
                "by_stage": [],
            }
        query: dict = {"run_id": run_id}
        if session_id is not None:
            query["session_id"] = session_id
        entries = list(self._col.find(query, {"_id": 0}))
        total = round(sum(e["cost_usd"] for e in entries), 6)
        stage_entries = [e for e in entries if e.get("stage")]
        return {
            "run_id": run_id,
            "total_usd": total,
            "by_node": entries,
            "by_node_summary": self._group_by(entries, "node_id"),
            "by_task_type": self._group_by(entries, "task_type"),
            "by_stage": self._group_by(stage_entries, "stage"),
        }

    def session_summary(self, session_id: str) -> dict:
        if self._col is None:
            return {"session_id": session_id, "total_usd": 0.0}
        entries = list(self._col.find({"session_id": session_id}, {"_id": 0}))
        total = round(sum(e["cost_usd"] for e in entries), 6)
        return {"session_id": session_id, "total_usd": total, "by_run": entries}
