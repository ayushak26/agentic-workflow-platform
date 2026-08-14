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
    assert ledger.run_summary("run_1") == {
        "run_id": "run_1",
        "total_usd": 0.0,
        "by_node": [],
        "by_node_summary": [],
        "by_task_type": [],
        "by_stage": [],
    }


def test_session_summary_sums_matching_entries():
    db = _FakeDb()
    ledger = CostLedger(db)
    ledger.record(_entry(session_id="sess_a", cost_usd=0.01))
    ledger.record(_entry(session_id="sess_a", run_id="run_2", cost_usd=0.02))
    ledger.record(_entry(session_id="sess_b", cost_usd=0.5))

    summary = ledger.session_summary("sess_a")

    assert summary["total_usd"] == 0.03
    assert len(summary["by_run"]) == 2


# ---------- new telemetry fields (task_type/provider/latency/fallback/stage) ----------

def test_record_persists_the_new_telemetry_fields():
    db = _FakeDb()
    ledger = CostLedger(db)
    ledger.record(_entry(
        task_type="structured_extraction",
        provider="anthropic",
        latency_ms=123.4,
        fallback_used=True,
        fallback_reason="claude-opus-5 unavailable (RateLimitError)",
        stage="generation",
    ))

    doc = db["cost_ledger"].docs[0]
    assert doc["task_type"] == "structured_extraction"
    assert doc["provider"] == "anthropic"
    assert doc["latency_ms"] == 123.4
    assert doc["fallback_used"] is True
    assert doc["fallback_reason"] == "claude-opus-5 unavailable (RateLimitError)"
    assert doc["stage"] == "generation"
    assert doc["no_model_charge"] is False


def test_ledger_entry_telemetry_fields_default_safely():
    entry = _entry()
    assert entry.task_type == "unknown"
    assert entry.provider == "unknown"
    assert entry.latency_ms is None
    assert entry.fallback_used is False
    assert entry.fallback_reason is None
    assert entry.stage is None
    assert entry.no_model_charge is False


# ---------- record_no_charge() ----------

def test_record_no_charge_writes_a_zero_cost_labeled_entry():
    db = _FakeDb()
    ledger = CostLedger(db)
    ledger.record_no_charge(
        run_id="run_1", session_id="sess_1", node_id="node_1",
        stage="hybrid_search", note="plain vector search, no model call",
    )

    doc = db["cost_ledger"].docs[0]
    assert doc["cost_usd"] == 0.0
    assert doc["no_model_charge"] is True
    assert doc["stage"] == "hybrid_search"
    assert doc["fallback_reason"] == "plain vector search, no model call"


# ---------- run_summary() breakdowns ----------

def test_run_summary_groups_by_node_task_type_and_stage():
    db = _FakeDb()
    ledger = CostLedger(db)
    ledger.record(_entry(
        node_id="rag_1", task_type="rerank_judge", stage="rerank",
        cost_usd=0.01, input_tokens=100, output_tokens=10, latency_ms=200.0,
    ))
    ledger.record(_entry(
        node_id="rag_1", task_type="reasoning", stage="generation",
        cost_usd=0.02, input_tokens=200, output_tokens=50, latency_ms=400.0,
    ))
    ledger.record(_entry(
        node_id="understand_request", task_type="structured_extraction", stage=None,
        cost_usd=0.005, input_tokens=50, output_tokens=20, latency_ms=100.0,
    ))
    ledger.record_no_charge(
        run_id="run_1", session_id="sess_1", node_id="rag_1", stage="hybrid_search",
    )

    summary = ledger.run_summary("run_1")

    by_node = {g["node_id"]: g for g in summary["by_node_summary"]}
    assert by_node["rag_1"]["calls"] == 3
    assert round(by_node["rag_1"]["cost_usd"], 6) == 0.03
    assert by_node["understand_request"]["calls"] == 1

    by_task = {g["task_type"]: g for g in summary["by_task_type"]}
    assert by_task["rerank_judge"]["cost_usd"] == 0.01
    assert by_task["reasoning"]["cost_usd"] == 0.02

    by_stage = {g["stage"]: g for g in summary["by_stage"]}
    # non-RAG entry (stage=None) must not pollute the stage breakdown
    assert set(by_stage) == {"rerank", "generation", "hybrid_search"}
    assert by_stage["rerank"]["cost_usd"] == 0.01
    assert by_stage["rerank"]["no_model_charge"] is False
    assert by_stage["hybrid_search"]["cost_usd"] == 0.0
    assert by_stage["hybrid_search"]["no_model_charge"] is True
    assert by_stage["generation"]["avg_latency_ms"] == 400.0


def test_group_by_reports_no_model_charge_only_when_every_entry_in_the_group_is_free():
    db = _FakeDb()
    ledger = CostLedger(db)
    ledger.record(_entry(node_id="n1", stage="rerank", cost_usd=0.01))
    ledger.record_no_charge(run_id="run_1", session_id="sess_1", node_id="n1", stage="rerank")

    summary = ledger.run_summary("run_1")
    by_stage = {g["stage"]: g for g in summary["by_stage"]}
    # mixed group (one priced call, one free call) is not "no charge" overall
    assert by_stage["rerank"]["no_model_charge"] is False
    assert by_stage["rerank"]["calls"] == 2
