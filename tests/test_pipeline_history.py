from __future__ import annotations

import pytest

from app.workflow.pipeline_history import (
    create_pipeline_run,
    get_pipeline_run,
    reconcile_stage_completion,
    record_stage_launch,
)
from app.workflow.run_history import upsert_run

from .fake_mongo import InMemoryDB

SESSION = "user@example.com"


async def _make_pipeline_with_two_stages(db) -> None:
    await create_pipeline_run(
        db,
        pipeline_run_id="pl-1",
        session_id=SESSION,
        pipeline_name="p",
        pipeline_yaml="name: p\nstages: []\n",
        pipeline_inputs={},
        stages=[
            {"id": "evidence", "workflow": "stage_one", "run_id": None, "status": "pending", "error": None},
            {"id": "drafts", "workflow": "stage_two", "run_id": None, "status": "pending", "error": None},
        ],
    )


@pytest.mark.asyncio
async def test_reconcile_ignores_run_ids_outside_any_pipeline():
    db = InMemoryDB()
    await upsert_run(db, "standalone-run", SESSION, status="completed")
    # Must not raise and must not create a pipeline doc out of nowhere.
    await reconcile_stage_completion(db, run_id="standalone-run", session_id=SESSION)
    assert db.collections.get("pipeline_runs") is None or not db["pipeline_runs"].docs


@pytest.mark.asyncio
async def test_reconcile_marks_pipeline_gated_after_non_final_stage_completes():
    db = InMemoryDB()
    await _make_pipeline_with_two_stages(db)
    await record_stage_launch(
        db, pipeline_run_id="pl-1", session_id=SESSION, stage_index=0, run_id="run-a",
    )
    await upsert_run(db, "run-a", SESSION, status="completed", outputs={"greeting": {"value": "hi"}})

    await reconcile_stage_completion(db, run_id="run-a", session_id=SESSION)

    doc = await get_pipeline_run(db, SESSION, "pl-1")
    assert doc["status"] == "gated"
    assert doc["current_stage_index"] == 0
    assert doc["stages"][0]["status"] == "completed"
    assert doc["stages"][1]["status"] == "pending"


@pytest.mark.asyncio
async def test_reconcile_marks_pipeline_completed_after_final_stage():
    db = InMemoryDB()
    await _make_pipeline_with_two_stages(db)
    await record_stage_launch(
        db, pipeline_run_id="pl-1", session_id=SESSION, stage_index=1, run_id="run-b",
    )
    await upsert_run(db, "run-b", SESSION, status="completed")

    await reconcile_stage_completion(db, run_id="run-b", session_id=SESSION)

    doc = await get_pipeline_run(db, SESSION, "pl-1")
    assert doc["status"] == "completed"
    assert doc["stages"][1]["status"] == "completed"


@pytest.mark.asyncio
async def test_reconcile_marks_pipeline_failed_on_stage_failure():
    db = InMemoryDB()
    await _make_pipeline_with_two_stages(db)
    await record_stage_launch(
        db, pipeline_run_id="pl-1", session_id=SESSION, stage_index=0, run_id="run-a",
    )
    await upsert_run(db, "run-a", SESSION, status="failed", error="boom")

    await reconcile_stage_completion(db, run_id="run-a", session_id=SESSION)

    doc = await get_pipeline_run(db, SESSION, "pl-1")
    assert doc["status"] == "failed"
    assert doc["stages"][0]["status"] == "failed"
    assert doc["stages"][0]["error"] == "boom"


@pytest.mark.asyncio
async def test_reconcile_keeps_pipeline_running_on_mid_stage_hitl_pause():
    """A stage's own internal HITL node pausing is not a pipeline-level gate
    — resolved via the normal /workflows/{run_id}/resume flow, independent of
    the pipeline's own advance step."""
    db = InMemoryDB()
    await _make_pipeline_with_two_stages(db)
    await record_stage_launch(
        db, pipeline_run_id="pl-1", session_id=SESSION, stage_index=0, run_id="run-a",
    )
    await upsert_run(db, "run-a", SESSION, status="paused")

    await reconcile_stage_completion(db, run_id="run-a", session_id=SESSION)

    doc = await get_pipeline_run(db, SESSION, "pl-1")
    assert doc["status"] == "running"
    assert doc["stages"][0]["status"] == "paused"
