"""Tests for app/observability/cost_ledger.py.

Uses a fake Mongo collection (no real Mongo) to test CostLedger's own read/write shape in
isolation.
"""
from __future__ import annotations

from app.observability.cost_ledger import CostLedger, LedgerEntry


class _FakeCollection:
    """Minimal in-memory stand-in for a pymongo Collection — just enough surface for
    CostLedger.record()/run_summary()/session_summary()."""

    def __init__(self):
        self.docs: list[dict] = []

    def insert_one(self, doc: dict) -> None:
        self.docs.append(dict(doc))

    def find(self, query: dict, projection: dict | None = None):
        def matches(doc: dict) -> bool:
            return all(doc.get(k) == v for k, v in query.items())

        results = [dict(d) for d in self.docs if matches(d)]
        if projection and projection.get("_id") == 0:
            for r in results:
                r.pop("_id", None)
        return results


class _FakeDb:
    def __init__(self):
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections.setdefault(name, _FakeCollection())


def _entry(**overrides) -> LedgerEntry:
    defaults = dict(
        run_id="run_1",
        session_id="sess_1",
        node_id="node_1",
        model="claude-opus-4-5",
        intended_model="quality",
        input_tokens=100,
        output_tokens=40,
        cost_usd=0.01,
    )
    defaults.update(overrides)
    return LedgerEntry(**defaults)


def test_ledger_entry_defaults_cost_source_to_estimated():
    entry = LedgerEntry(
        run_id="r",
        session_id="s",
        node_id="n",
        model="m",
        intended_model="m",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
    )
    assert entry.cost_source == "estimated"


def test_record_persists_cost_source():
    db = _FakeDb()
    ledger = CostLedger(db)
    ledger.record(_entry(cost_source="provider_reported"))

    doc = db["cost_ledger"].docs[0]
    assert doc["cost_source"] == "provider_reported"


def test_record_is_a_noop_when_db_is_none():
    ledger = CostLedger(None)
    ledger.record(_entry())  # must not raise


def test_calculate_uses_model_pricing_table():
    cost = CostLedger.calculate("claude-haiku-4-5", input_tokens=1000, output_tokens=1000)
    assert cost > 0


def test_calculate_falls_back_to_generic_default_for_unknown_model():
    cost = CostLedger.calculate("some-unmapped-model", input_tokens=1000, output_tokens=1000)
    assert cost > 0


def test_run_summary_sums_matching_entries():
    db = _FakeDb()
    ledger = CostLedger(db)
    ledger.record(_entry(run_id="run_a", cost_usd=0.01))
    ledger.record(_entry(run_id="run_a", node_id="node_2", cost_usd=0.02))
    ledger.record(_entry(run_id="run_b", cost_usd=0.5))

    summary = ledger.run_summary("run_a")

    assert summary["total_usd"] == 0.03
    assert len(summary["by_node"]) == 2


def test_run_summary_is_zero_when_db_is_none():
    ledger = CostLedger(None)
    assert ledger.run_summary("run_1") == {"run_id": "run_1", "total_usd": 0.0, "by_node": []}


def test_session_summary_sums_matching_entries():
    db = _FakeDb()
    ledger = CostLedger(db)
    ledger.record(_entry(session_id="sess_a", cost_usd=0.01))
    ledger.record(_entry(session_id="sess_a", run_id="run_2", cost_usd=0.02))
    ledger.record(_entry(session_id="sess_b", cost_usd=0.5))

    summary = ledger.session_summary("sess_a")

    assert summary["total_usd"] == 0.03
    assert len(summary["by_run"]) == 2
