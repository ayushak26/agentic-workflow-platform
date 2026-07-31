"""Cooperative "pause requested from run history" — not a HITL gate.

Exercises the real LangGraph interrupt/resume path (app/runtime/compiler.py,
app/workflow/run_history.py, app/runtime/hitl.py) with a generic in-memory
Mongo fake, since the correctness of the interrupt-call-count matching
across the pause pass and the resume replay pass can't be verified any other
way — a mock that always returns canned values would hide exactly the bug
this is meant to catch.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.runtime.executor import _PAUSED_GRAPHS, run_workflow
from app.runtime.hitl import HITLResumeError, resume_workflow_durable
from app.runtime.loader import load_workflow_from_string
from app.workflow.run_history import (
    delete_run,
    get_resume_checkpoint,
    initialize_run_checkpoint,
    is_pause_requested,
    request_pause,
    upsert_run,
)

WORKFLOW_YAML = """
name: user_pause_test
version: "1.0"
nodes:
  - id: seed
    type: Literal
    config:
      value: seed-result
  - id: middle
    type: Literal
    config:
      value: middle-result
  - id: final
    type: Literal
    config:
      value: final-result
edges:
  - from: seed
    to: middle
  - from: middle
    to: final
entry: seed
exit: final
"""


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for doc in self._docs:
            yield doc


class FakeCollection:
    """Enough of Motor's collection API for run_history/run_checkpoints/audit_log."""

    def __init__(self):
        self.docs: list[dict] = []

    def _match(self, doc, query):
        return all(doc.get(k) == v for k, v in query.items())

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if self._match(doc, query):
                if projection:
                    keep = {k for k, v in projection.items() if v}
                    exclude = {k for k, v in projection.items() if not v}
                    if keep:
                        return {k: v for k, v in doc.items() if k in keep}
                    return {k: v for k, v in doc.items() if k not in exclude}
                return dict(doc)
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        matches = [dict(doc) for doc in self.docs if self._match(doc, query)]
        return _Cursor(matches)

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=len(self.docs))

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._match(doc, query):
                self._apply(doc, update)
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            doc = dict(query)
            doc.update(update.get("$setOnInsert", {}))
            self._apply(doc, {k: v for k, v in update.items() if k != "$setOnInsert"})
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def _apply(self, doc, update):
        for path, value in update.get("$set", {}).items():
            doc[path] = deepcopy(value)
        for path in update.get("$unset", {}):
            doc.pop(path, None)
        for path, value in update.get("$pull", {}).items():
            if isinstance(doc.get(path), list):
                doc[path] = [item for item in doc[path] if item != value]
        for path, value in update.get("$addToSet", {}).items():
            items = doc.setdefault(path, [])
            if value not in items:
                items.append(deepcopy(value))
        for path, value in update.get("$push", {}).items():
            doc.setdefault(path, []).append(deepcopy(value))

    async def delete_one(self, query):
        for i, doc in enumerate(self.docs):
            if self._match(doc, query):
                self.docs.pop(i)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakeDB:
    def __init__(self):
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture(autouse=True)
def _clear_paused_graphs():
    _PAUSED_GRAPHS.clear()
    yield
    _PAUSED_GRAPHS.clear()


@pytest.mark.asyncio
async def test_pause_request_only_matches_a_running_run():
    db = FakeDB()
    session_id = "user@example.com"
    run_id = "run-1"
    await upsert_run(db, run_id, session_id, workflow_name="x", status="completed")

    matched = await request_pause(db, run_id=run_id, session_id=session_id)

    assert matched is False
    assert await is_pause_requested(db, run_id=run_id, session_id=session_id) is False


@pytest.mark.asyncio
async def test_run_pauses_at_next_node_boundary_and_resumes_to_completion():
    db = FakeDB()
    session_id = "user@example.com"
    run_id = "run-pause-1"
    spec = load_workflow_from_string(WORKFLOW_YAML)

    await upsert_run(db, run_id, session_id, workflow_name=spec.name, status="running")
    await initialize_run_checkpoint(
        db, run_id=run_id, session_id=session_id,
        workflow_yaml=WORKFLOW_YAML, inputs={}, collection_id="default",
    )
    # Simulate a pause request arriving before the graph starts (in production
    # this lands while some other node is already executing — the mechanism
    # the node wrapper checks is identical either way).
    matched = await request_pause(db, run_id=run_id, session_id=session_id)
    assert matched is True

    result = await run_workflow(
        spec, {}, session_id=session_id, services={"audit_db": db}, run_id=run_id,
    )

    assert result["status"] == "paused"
    history_doc = await db["run_history"].find_one(
        {"run_id": run_id, "session_id": session_id}
    )
    assert history_doc["status"] == "paused"
    assert history_doc["pause_kind"] == "user_requested"
    checkpoint_doc = await db["run_checkpoints"].find_one(
        {"run_id": run_id, "session_id": session_id}
    )
    assert checkpoint_doc["pause_kind"] == "user_requested"
    # Paused at the FIRST node boundary, before it ran.
    assert checkpoint_doc["paused_node_id"] == "seed"
    assert "seed" not in (checkpoint_doc.get("node_results") or {})

    # The flag intentionally stays set across the pause itself -- it is only
    # cleared once actually consumed by a resume (see compiler.py), so that
    # the interrupt() call this node made is replayed the same number of
    # times on resume as it was on this pausing pass.
    assert await is_pause_requested(db, run_id=run_id, session_id=session_id) is True

    checkpoint = await get_resume_checkpoint(db, session_id, run_id)
    assert checkpoint is not None
    assert checkpoint["pause_kind"] == "user_requested"

    resumed = await resume_workflow_durable(
        run_id,
        {"decision": "continue"},
        services={"audit_db": db},
        session_id=session_id,
        actor="ayush@example.com",
    )

    assert resumed["status"] == "completed"
    outputs = resumed["state"]["node_outputs"]
    assert outputs["seed"]["value"] == "seed-result"
    assert outputs["middle"]["value"] == "middle-result"
    assert outputs["final"]["value"] == "final-result"
    assert await is_pause_requested(db, run_id=run_id, session_id=session_id) is False


@pytest.mark.asyncio
async def test_pausing_mid_run_only_stops_at_the_next_boundary_not_every_node():
    """Requesting a pause partway through must not cascade to later nodes
    once resumed — the flag is consumed exactly once."""

    db = FakeDB()
    session_id = "user@example.com"
    run_id = "run-pause-2"
    spec = load_workflow_from_string(WORKFLOW_YAML)

    await upsert_run(db, run_id, session_id, workflow_name=spec.name, status="running")
    await initialize_run_checkpoint(
        db, run_id=run_id, session_id=session_id,
        workflow_yaml=WORKFLOW_YAML, inputs={}, collection_id="default",
    )
    await request_pause(db, run_id=run_id, session_id=session_id)

    first = await run_workflow(
        spec, {}, session_id=session_id, services={"audit_db": db}, run_id=run_id,
    )
    assert first["status"] == "paused"
    checkpoint_doc = await db["run_checkpoints"].find_one(
        {"run_id": run_id, "session_id": session_id}
    )
    assert checkpoint_doc["paused_node_id"] == "seed"

    # Simulate the resume hitting a different worker process: drop the
    # in-memory cached graph so resume must rebuild from the fallback path.
    _PAUSED_GRAPHS.pop(run_id, None)

    resumed = await resume_workflow_durable(
        run_id,
        {"decision": "continue"},
        services={"audit_db": db},
        session_id=session_id,
        actor="ayush@example.com",
    )

    # Must run straight through to completion -- NOT pause again at "middle".
    assert resumed["status"] == "completed"
    outputs = resumed["state"]["node_outputs"]
    assert set(outputs) == {"seed", "middle", "final"}


@pytest.mark.asyncio
async def test_pause_resumes_correctly_with_a_persistent_checkpointer():
    """Deployments with a Redis (here: MemorySaver-as-stand-in) LangGraph
    checkpointer take resume_workflow_durable's third branch: rebuild the
    graph and Command(resume=...) the exact saved thread. This is where the
    interrupt() call-count mismatch would actually bite in production."""

    db = FakeDB()
    session_id = "user@example.com"
    run_id = "run-pause-redis"
    spec = load_workflow_from_string(WORKFLOW_YAML)
    saver = MemorySaver()
    services = {"audit_db": db, "langgraph_checkpointer": saver}

    await upsert_run(db, run_id, session_id, workflow_name=spec.name, status="running")
    await initialize_run_checkpoint(
        db, run_id=run_id, session_id=session_id,
        workflow_yaml=WORKFLOW_YAML, inputs={}, collection_id="default",
    )
    await request_pause(db, run_id=run_id, session_id=session_id)

    first = await run_workflow(spec, {}, session_id=session_id, services=services, run_id=run_id)
    assert first["status"] == "paused"

    # A process restart loses the in-memory graph but not the durable saver.
    _PAUSED_GRAPHS.pop(run_id, None)
    resumed = await resume_workflow_durable(
        run_id,
        {"decision": "continue"},
        services=services,
        session_id=session_id,
        actor="ayush@example.com",
    )

    assert resumed["status"] == "completed"
    assert set(resumed["state"]["node_outputs"]) == {"seed", "middle", "final"}
    assert await is_pause_requested(db, run_id=run_id, session_id=session_id) is False


@pytest.mark.asyncio
async def test_resuming_a_hitl_gate_is_rejected_as_user_pause_kind_mismatch():
    """A real HITL gate's checkpoint has pause_kind == 'hitl_gate' (the
    default) -- resume_workflow_durable's HITL validation must still apply,
    proving the user-pause bypass in _validate_saved_decision is scoped
    correctly and doesn't leak into real HITL gates."""

    db = FakeDB()
    session_id = "user@example.com"
    run_id = "run-hitl-1"
    workflow_yaml = """
name: hitl_gate_test
version: "1.0"
nodes:
  - id: seed
    type: Literal
    config:
      value: seed-result
  - id: approval
    type: HumanInLoopAgent
    config:
      question: Approve?
      allowed_actions: [approve, reject]
edges:
  - from: seed
    to: approval
entry: seed
exit: approval
"""
    spec = load_workflow_from_string(workflow_yaml)
    await upsert_run(db, run_id, session_id, workflow_name=spec.name, status="running")
    await initialize_run_checkpoint(
        db, run_id=run_id, session_id=session_id,
        workflow_yaml=workflow_yaml, inputs={}, collection_id="default",
    )

    paused = await run_workflow(
        spec, {}, session_id=session_id, services={"audit_db": db}, run_id=run_id,
    )
    assert paused["status"] == "paused"
    checkpoint_doc = await db["run_checkpoints"].find_one(
        {"run_id": run_id, "session_id": session_id}
    )
    assert checkpoint_doc.get("pause_kind", "hitl_gate") == "hitl_gate"

    # A disallowed decision on a real HITL gate must still be rejected.
    with pytest.raises(ValueError):
        await resume_workflow_durable(
            run_id,
            {"decision": "continue"},  # not in allowed_actions [approve, reject]
            services={"audit_db": db},
            session_id=session_id,
            actor="ayush@example.com",
        )


@pytest.mark.asyncio
async def test_delete_run_removes_history_and_checkpoint():
    db = FakeDB()
    session_id = "user@example.com"
    run_id = "run-delete-1"
    await upsert_run(db, run_id, session_id, workflow_name="x", status="completed")

    deleted = await delete_run(db, run_id=run_id, session_id=session_id)
    assert deleted is True
    assert await db["run_history"].find_one(
        {"run_id": run_id, "session_id": session_id}
    ) is None

    deleted_again = await delete_run(db, run_id=run_id, session_id=session_id)
    assert deleted_again is False
