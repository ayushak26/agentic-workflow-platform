from __future__ import annotations

import pytest

from app.db.migrations import (
    MIGRATIONS,
    _REMOVED_RUN_FIELDS,
    _remove_business_and_pipeline_persistence_v3,
)


class Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    async def update_many(self, _filter, update):
        for doc in self.docs:
            for field in update.get("$unset", {}):
                doc.pop(field, None)


class Database:
    def __init__(self):
        self.collections = {
            "run_history": Collection(),
            "pipeline_runs": Collection([{"pipeline_run_id": "legacy-pipeline"}]),
            "business_narrations": Collection([{"run_id": "run-1"}]),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())

    async def drop_collection(self, name):
        self.collections.pop(name, None)


@pytest.mark.asyncio
async def test_removed_product_migration_drops_legacy_data_and_preserves_workflow_data():
    db = Database()
    workflow_yaml = """
name: Keep Workflow Stages
nodes:
  - id: prepare
    type: Literal
    config: {value: ready}
    experience:
      stage_id: prepare
entry: prepare
"""
    db["run_history"].docs.append({
        "run_id": "run-1",
        "workflow_name": "Keep Workflow Stages",
        "workflow_yaml": workflow_yaml,
        "outputs": {"prepare": {"value": "ready"}},
        "pipeline_run_id": "legacy-pipeline",
        "pipeline_name": "Legacy Pipeline",
        "stage_id": "legacy-stage",
        "stage_index": 0,
        "business_notes": [{"text": "legacy note"}],
        "route_overrides": [{"route": "legacy route"}],
        "assigned_to": "legacy owner",
        "fact_edits": [{"field": "legacy"}],
        "stale_decisions": ["legacy-decision"],
    })

    await _remove_business_and_pipeline_persistence_v3(db)
    await _remove_business_and_pipeline_persistence_v3(db)

    run = db["run_history"].docs[0]
    assert set(run).isdisjoint(_REMOVED_RUN_FIELDS)
    assert run["run_id"] == "run-1"
    assert run["workflow_name"] == "Keep Workflow Stages"
    assert run["workflow_yaml"] == workflow_yaml
    assert "experience:\n      stage_id: prepare" in run["workflow_yaml"]
    assert run["outputs"] == {"prepare": {"value": "ready"}}
    assert "pipeline_runs" not in db.collections
    assert "business_narrations" not in db.collections
    assert "experience.stage_id" not in _REMOVED_RUN_FIELDS


def test_removed_product_migration_is_registered_after_existing_backfills():
    migration_ids = [migration.migration_id for migration in MIGRATIONS]

    assert migration_ids == [
        "0001_run_documents_v1",
        "0002_knowledge_resources_v2",
        "0003_remove_business_pipeline_persistence",
    ]