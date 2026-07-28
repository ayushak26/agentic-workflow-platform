from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.llm.model_catalog import (
    MODEL_PRICING as CATALOG_MODEL_PRICING,
    estimate_model_cost,
)
from app.observability.logging import get_logger

logger = get_logger(__name__)
MODEL_PRICING = {
    **CATALOG_MODEL_PRICING,
    "claude-opus-4-8": (0.005, 0.025),
    "text-embedding-3-small": (0.00002, 0.0),
}


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
    selected_model: str | None = None
    selection_mode: str = "manual"
    selection_reason: str | None = None
    task_kind: str | None = None
    complexity: str | None = None
    cache_hit:      bool = False
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CostLedger:
    def __init__(self, db: Any):
        self._col = db["cost_ledger"] if db is not None else None

    @staticmethod
    def calculate(model: str, input_tokens: int, output_tokens: int) -> float:
        if model in MODEL_PRICING:
            p_in, p_out = MODEL_PRICING[model]
            return round(
                (input_tokens * p_in + output_tokens * p_out) / 1000,
                6,
            )
        return estimate_model_cost(model, input_tokens, output_tokens)

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
                "cost_usd":       entry.cost_usd,
                "selected_model": entry.selected_model,
                "selection_mode": entry.selection_mode,
                "selection_reason": entry.selection_reason,
                "task_kind": entry.task_kind,
                "complexity": entry.complexity,
                "cache_hit":      entry.cache_hit,
                "ts":             entry.ts,
            })
        logger.info(
            "llm_cost",
            run_id=entry.run_id,
            node_id=entry.node_id,
            model=entry.model,
            intended_model=entry.intended_model,
            selected_model=entry.selected_model,
            selection_mode=entry.selection_mode,
            cost_usd=entry.cost_usd,
            cache_hit=entry.cache_hit,
        )

    def run_summary(self, run_id: str, session_id: str) -> dict:
        if self._col is None:
            return {"run_id": run_id, "total_usd": 0.0, "by_node": []}
        entries = list(
            self._col.find(
                {"run_id": run_id, "session_id": session_id},
                {"_id": 0},
            )
        )
        total = round(sum(e["cost_usd"] for e in entries), 6)
        return {"run_id": run_id, "total_usd": total, "by_node": entries}

    def session_summary(self, session_id: str) -> dict:
        if self._col is None:
            return {"session_id": session_id, "total_usd": 0.0}
        entries = list(self._col.find({"session_id": session_id}, {"_id": 0}))
        total = round(sum(e["cost_usd"] for e in entries), 6)
        return {"session_id": session_id, "total_usd": total, "by_run": entries}

    def daily_spend(self, session_id: str | None = None) -> float:
        """Return UTC-day spend globally or for one authenticated session."""

        if self._col is None:
            return 0.0
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        match: dict[str, Any] = {"ts": {"$gte": start}}
        if session_id is not None:
            match["session_id"] = session_id
        rows = list(
            self._col.aggregate(
                [
                    {"$match": match},
                    {"$group": {"_id": None, "total": {"$sum": "$cost_usd"}}},
                ]
            )
        )
        return float(rows[0]["total"]) if rows else 0.0
