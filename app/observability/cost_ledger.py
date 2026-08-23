"""Cost ledger module.

Part of the observability: structured logging, prometheus metrics, tracing, and the cost ledger.

Public symbols: LedgerEntry, configure_pricing_db, CostLedger.
"""
from __future__ import annotations
import time
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
ANTHROPIC_CACHE_WRITE_MULTIPLIER = 2.0
ANTHROPIC_CACHE_READ_MULTIPLIER = 0.1
# OpenAI's automatic prompt caching only ever produces read hits (no
# separate write cost) and bills them at half the standard input price.
OPENAI_CACHE_READ_MULTIPLIER = 0.5


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
    """Provides the LedgerEntry behaviour.

    Attributes:
        run_id (str).
        session_id (str).
        node_id (str).
        model (str).
        intended_model (str).
        input_tokens (int).
        output_tokens (int).
        cost_usd (float).
    """
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
    # Which knowledge collection this call's retrieval/generation was scoped to (the same
    # RegistryLLMGateway._collection_id already threaded through for entity tokenization) —
    # "default" for calls that never set one explicitly, matching that field's own default.
    # Enables cost breakdowns filtered by collection_id; per-FILE attribution isn't tracked
    # at this layer (a single LLM call can draw on chunks from many files in a collection).
    collection_id: str = "default"
    # Denormalized at call time from RegistryLLMGateway._workflow_name (set via
    # with_context) so cost breakdowns never depend on a run_history join — a
    # join that misses for any run_id never persisted there (node tests, eval
    # runs, assist chat, workflow generation) and previously collapsed all of
    # those into a misleading "unknown" bucket. None for legacy entries
    # recorded before this field existed.
    workflow_name: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Admin-configurable pricing overrides (app/api/cost_admin.py) — a Mongo-backed table an
# operator can edit live, layered on top of the hardcoded MODEL_PRICING defaults above.
# Read with a short TTL cache rather than on every call: calculate() runs on the hot LLM-call
# path (registry.py::_record_cost, itself already wrapped in a never-raise try/except), so a
# stale-by-up-to-60s price is a far better failure mode than adding synchronous Mongo latency
# — or a hard dependency — to every completion. Any read failure silently keeps the
# last-known-good cache (or empty, on first use) and falls through to MODEL_PRICING.
# ---------------------------------------------------------------------------
_PRICING_OVERRIDE_TTL_SECONDS = 60.0
_pricing_db: Any = None
_pricing_override_cache: dict[str, tuple[float, float]] = {}
_pricing_override_fetched_at: float = 0.0


def configure_pricing_db(db: Any) -> None:
    """Called once from app/main.py's lifespan, right after Mongo is available."""
    global _pricing_db
    _pricing_db = db


def _refresh_pricing_overrides_if_stale() -> None:
    """Refresh the pricing overrides if stale."""
    global _pricing_override_cache, _pricing_override_fetched_at
    if _pricing_db is None:
        return
    if time.monotonic() - _pricing_override_fetched_at < _PRICING_OVERRIDE_TTL_SECONDS:
        return
    try:
        docs = list(_pricing_db["pricing_overrides"].find({}, {"_id": 0}))
        _pricing_override_cache = {
            doc["model"]: (doc["input_usd_per_1k"], doc["output_usd_per_1k"])
            for doc in docs
        }
    except Exception:
        pass  # keep the last-known-good cache; a pricing lookup must never raise
    finally:
        _pricing_override_fetched_at = time.monotonic()


class CostLedger:
    """Provides the CostLedger behaviour."""
    def __init__(self, db: Any):
        """Initialize the CostLedger.

        Args:
            db (Any): Mongo database handle.
        """
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
        """Compute the result.

        Args:
            model (str): Model name.
            input_tokens (int): Input token count.
            output_tokens (int): Output token count.
            cache_creation_input_tokens (int): The cache creation input tokens (optional, default 0).
            cache_read_input_tokens (int): The cache read input tokens (optional, default 0).

        Returns:
            float: The result.
        """
        _refresh_pricing_overrides_if_stale()
        p_in, p_out = _pricing_override_cache.get(model) or MODEL_PRICING.get(
            model, (0.005, 0.015)
        )
        cost = input_tokens * p_in + output_tokens * p_out
        if model.startswith("claude"):
            cost += cache_creation_input_tokens * p_in * ANTHROPIC_CACHE_WRITE_MULTIPLIER
            cost += cache_read_input_tokens * p_in * ANTHROPIC_CACHE_READ_MULTIPLIER
        else:
            # Non-Anthropic models never report cache_creation (no write
            # concept); cache_read here is OpenAI's automatic-cache hit count.
            cost += cache_read_input_tokens * p_in * OPENAI_CACHE_READ_MULTIPLIER
        return round(cost / 1000, 6)

    def record(self, entry: LedgerEntry) -> None:
        """Record the result.

        Args:
            entry (LedgerEntry): Ledger entry.
        """
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
                "collection_id":  entry.collection_id,
                "workflow_name":  entry.workflow_name,
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
            collection_id=entry.collection_id,
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
        """Group the by.

        Args:
            entries (list[dict]): Entries to process.
            key (str): Lookup key.

        Returns:
            list[dict]: The by.
        """
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
        """Run the summary.

        Args:
            run_id (str): Workflow run identifier.
            session_id (str | None): Session scope the record belongs to (optional, default None).

        Returns:
            dict: The summary.
        """
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
        """Compute the session summary.

        Args:
            session_id (str): Session scope the record belongs to.

        Returns:
            dict: The summary.
        """
        if self._col is None:
            return {"session_id": session_id, "total_usd": 0.0}
        entries = list(self._col.find({"session_id": session_id}, {"_id": 0}))
        total = round(sum(e["cost_usd"] for e in entries), 6)
        return {"session_id": session_id, "total_usd": total, "by_run": entries}

    def query(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        collection_id: str | None = None,
    ) -> list[dict]:
        """Raw entries across the whole ledger (not scoped to one run/session), for
        account-wide reporting (app/api/cost_admin.py). Callers group/aggregate themselves —
        this stays a plain, filtered fetch so it composes with any breakdown."""
        if self._col is None:
            return []
        query: dict[str, Any] = {}
        ts_filter: dict[str, datetime] = {}
        if since is not None:
            ts_filter["$gte"] = since
        if until is not None:
            ts_filter["$lte"] = until
        if ts_filter:
            query["ts"] = ts_filter
        if collection_id is not None:
            query["collection_id"] = collection_id
        return list(self._col.find(query, {"_id": 0}))
