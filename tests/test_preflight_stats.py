from __future__ import annotations

import pytest

from app.workflow.preflight_stats import (
    _MIN_SAMPLE_FOR_ESTIMATES,
    preflight_stats,
    record_attempt,
)
from tests.fake_mongo import InMemoryDB


@pytest.mark.asyncio
async def test_record_attempt_is_a_no_op_without_a_db():
    # Telemetry must never require a db to be present.
    await record_attempt(
        None, source="validate", workflow_name="x", success=True, error_codes=[],
    )


@pytest.mark.asyncio
async def test_stats_reports_not_enough_data_below_threshold():
    db = InMemoryDB()
    for _ in range(_MIN_SAMPLE_FOR_ESTIMATES - 1):
        await record_attempt(
            db, source="validate", workflow_name="x", success=True, error_codes=[],
        )

    result = await preflight_stats(db)

    assert result["available"] is True
    assert result["enough_data"] is False
    assert result["sample_size"] == _MIN_SAMPLE_FOR_ESTIMATES - 1


@pytest.mark.asyncio
async def test_stats_computes_success_rate_and_recurring_codes():
    db = InMemoryDB()
    # Two failed validate calls with the same recurring code, one that
    # succeeded on its own.
    await record_attempt(
        db, source="validate", workflow_name="a", success=False,
        error_codes=["TEMPLATE_UNKNOWN_OUTPUT_FIELD"],
    )
    await record_attempt(
        db, source="validate", workflow_name="b", success=False,
        error_codes=["TEMPLATE_UNKNOWN_OUTPUT_FIELD"],
    )
    await record_attempt(
        db, source="validate", workflow_name="c", success=True, error_codes=[],
    )
    # An autofix call that started broken and was fully resolved
    # deterministically — the initial code should still count toward
    # "recurring", even though it isn't in the final error_codes.
    await record_attempt(
        db, source="autofix", workflow_name="d", success=True,
        error_codes=[],
        initial_error_codes=["TEMPLATE_UNKNOWN_OUTPUT_FIELD"],
        deterministic_fixes_applied=1,
    )
    # A generate call that failed for an unrelated reason.
    await record_attempt(
        db, source="generate", workflow_name="e", success=False,
        error_codes=["NO_TERMINAL_NODE"],
    )

    result = await preflight_stats(db)

    assert result["enough_data"] is True
    assert result["sample_size"] == 5
    assert result["success_rate"] == pytest.approx(2 / 5)
    assert result["by_source"]["validate"] == {"total": 3, "success_rate": pytest.approx(1 / 3, abs=1e-4)}
    assert result["by_source"]["autofix"] == {"total": 1, "success_rate": 1.0}
    assert result["by_source"]["generate"] == {"total": 1, "success_rate": 0.0}

    top_codes = dict(result["top_recurring_error_codes"])
    assert top_codes["TEMPLATE_UNKNOWN_OUTPUT_FIELD"] == 3  # 2 validate + 1 autofix's initial
    assert top_codes["NO_TERMINAL_NODE"] == 1

    top_unresolved = dict(result["top_unresolved_error_codes"])
    assert top_unresolved["TEMPLATE_UNKNOWN_OUTPUT_FIELD"] == 2  # autofix's got resolved, doesn't count here
    assert result["autofix_resolved_deterministically"] == 1
    assert result["autofix_resolved_by_llm"] == 0


@pytest.mark.asyncio
async def test_stats_unavailable_without_a_db():
    result = await preflight_stats(None)
    assert result["available"] is False
