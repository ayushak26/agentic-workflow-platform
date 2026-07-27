from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5":          (0.005,   0.025),
    "claude-opus-4-8":        (0.005,   0.025),
    "claude-sonnet-4-5":      (0.003,   0.015),
    "claude-haiku-4-5":       (0.00025, 0.00125),
    "gpt-5.6-sol":            (0.005,   0.030),
    "gpt-5":                  (0.005,   0.020),
    "gpt-5-mini":             (0.0005,  0.0015),
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
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CostLedger:
    def __init__(self, db: Any):
        self._col = db["cost_ledger"] if db is not None else None

    @staticmethod
    def calculate(model: str, input_tokens: int, output_tokens: int) -> float:
        p_in, p_out = MODEL_PRICING.get(model, (0.005, 0.015))
        return round((input_tokens * p_in + output_tokens * p_out) / 1000, 6)

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
                "ts":             entry.ts,
            })
        logger.info(
            "llm_cost",
            run_id=entry.run_id,
            node_id=entry.node_id,
            model=entry.model,
            intended_model=entry.intended_model,
            cost_usd=entry.cost_usd,
        )

    def run_summary(self, run_id: str) -> dict:
        if self._col is None:
            return {"run_id": run_id, "total_usd": 0.0, "by_node": []}
        entries = list(self._col.find({"run_id": run_id}, {"_id": 0}))
        total = round(sum(e["cost_usd"] for e in entries), 6)
        return {"run_id": run_id, "total_usd": total, "by_node": entries}

    def session_summary(self, session_id: str) -> dict:
        if self._col is None:
            return {"session_id": session_id, "total_usd": 0.0}
        entries = list(self._col.find({"session_id": session_id}, {"_id": 0}))
        total = round(sum(e["cost_usd"] for e in entries), 6)
        return {"session_id": session_id, "total_usd": total, "by_run": entries}
