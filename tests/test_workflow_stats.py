from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.workflow.run_history import workflow_stats
from tests.fake_mongo import InMemoryDB

SESSION = "user@example.com"
WORKFLOW = "lead_enrichment_qualification"


async def _seed(
    db: InMemoryDB,
    *,
    run_id: str,
    status: str,
    workflow_name: str = WORKFLOW,
    session_id: str = SESSION,
    duration_s: float | None = None,
    error: str | None = None,
    failed_node: str | None = None,
    age_seconds: float = 0.0,
) -> None:
    await db["run_history"].insert_one({
        "run_id": run_id,
        "session_id": session_id,
        "workflow_name": workflow_name,
        "status": status,
        "duration_s": duration_s,
        "error": error,
        "failed_node": failed_node,
        "created_at": datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    })


@pytest.mark.asyncio
async def test_reports_not_enough_data_below_minimum_sample():
    db = InMemoryDB()
    await _seed(db, run_id="r1", status="completed", duration_s=10.0)
    await _seed(db, run_id="r2", status="completed", duration_s=12.0)

    stats = await workflow_stats(db, SESSION, WORKFLOW)

    assert stats["enough_data_for_estimates"] is False
    assert stats["median_duration_s"] is None
    assert stats["success_rate"] is None
    assert stats["sample_size"] == 2


@pytest.mark.asyncio
async def test_computes_success_rate_median_duration_and_common_failure():
    db = InMemoryDB()
    await _seed(db, run_id="r1", status="completed", duration_s=10.0, age_seconds=400)
    await _seed(db, run_id="r2", status="completed", duration_s=20.0, age_seconds=300)
    await _seed(db, run_id="r3", status="completed", duration_s=30.0, age_seconds=200)
    await _seed(
        db, run_id="r4", status="failed",
        error="boom", failed_node="score_lead", age_seconds=100,
    )
    await _seed(
        db, run_id="r5", status="failed",
        error="boom", failed_node="score_lead", age_seconds=50,
    )

    stats = await workflow_stats(db, SESSION, WORKFLOW)

    assert stats["enough_data_for_estimates"] is True
    assert stats["completed_runs"] == 3
    assert stats["failed_runs"] == 2
    assert stats["success_rate"] == pytest.approx(3 / 5)
    assert stats["median_duration_s"] == 20.0
    assert stats["most_common_failure"] == "score_lead"
    assert stats["last_run_status"] == "failed"  # most recent by created_at


@pytest.mark.asyncio
async def test_running_runs_do_not_count_toward_success_or_failure():
    db = InMemoryDB()
    await _seed(db, run_id="r1", status="completed", duration_s=10.0, age_seconds=30)
    await _seed(db, run_id="r2", status="completed", duration_s=10.0, age_seconds=20)
    await _seed(db, run_id="r3", status="running", age_seconds=5)

    stats = await workflow_stats(db, SESSION, WORKFLOW)

    assert stats["sample_size"] == 3
    assert stats["completed_runs"] == 2
    assert stats["failed_runs"] == 0
    assert stats["enough_data_for_estimates"] is False  # only 2 terminal runs


@pytest.mark.asyncio
async def test_stats_are_scoped_to_the_named_workflow_only():
    db = InMemoryDB()
    await _seed(db, run_id="r1", status="completed", duration_s=10.0, workflow_name=WORKFLOW)
    await _seed(db, run_id="r2", status="completed", duration_s=10.0, workflow_name=WORKFLOW)
    await _seed(db, run_id="r3", status="completed", duration_s=10.0, workflow_name=WORKFLOW)
    await _seed(db, run_id="other-1", status="failed", workflow_name="other_workflow")

    stats = await workflow_stats(db, SESSION, WORKFLOW)

    assert stats["sample_size"] == 3
    assert stats["failed_runs"] == 0


@pytest.mark.asyncio
async def test_stats_are_scoped_to_the_session_only():
    db = InMemoryDB()
    await _seed(db, run_id="r1", status="completed", duration_s=10.0, session_id=SESSION)
    await _seed(db, run_id="r2", status="completed", duration_s=10.0, session_id=SESSION)
    await _seed(db, run_id="r3", status="completed", duration_s=10.0, session_id=SESSION)
    await _seed(db, run_id="other-session-1", status="failed", session_id="someone-else@example.com")

    stats = await workflow_stats(db, SESSION, WORKFLOW)

    assert stats["sample_size"] == 3
    assert stats["failed_runs"] == 0


@pytest.mark.asyncio
async def test_no_runs_at_all_reports_zero_sample_not_an_error():
    db = InMemoryDB()

    stats = await workflow_stats(db, SESSION, "never_run_workflow")

    assert stats["sample_size"] == 0
    assert stats["enough_data_for_estimates"] is False
    assert stats["last_run_at"] is None
    assert stats["last_run_status"] is None
