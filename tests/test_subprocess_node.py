"""SubprocessAgent — calls another saved workflow as a genuinely independent
run, not nested inside this run's own graph invocation.

The product claim under test: the child launches via the same path a
person-started run takes (BackgroundRunManager + run_workflow), the parent
pauses via LangGraph's own interrupt()/resume machinery (not new plumbing),
and the child's terminal result reaches the parent through the callback
delivery path (app.workflow.subprocess_callback) — the exact same
resume_workflow_durable/finalize_run_result functions a human-driven HITL
resume already goes through. Also covers the two concrete failure modes a
naive design hits: launching the child twice on an in-process resume replay,
and a subprocess reference chain deep/cyclic enough to blow the interpreter's
recursion limit.
"""
from __future__ import annotations

import asyncio
import copy
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from app.config import settings
from app.nodes.subprocess import SubprocessAgent, _resolve_child_inputs
from app.runtime.executor import _PAUSED_GRAPHS, run_workflow
from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import preflight_workflow_yaml
from app.runtime.schema import WorkflowInputSpec
from app.workflow import subprocess_launches
from app.workflow.orchestration import BackgroundRunManager, start_new_run_record


# --------------------------------------------------------------------------
# A general-purpose fake Mongo, because run_history's own queries are
# compound (run_id + session_id, no _id) while subprocess_launches' are
# _id-keyed — one fake collection type needs to support both correctly,
# including the $push record_checkpoint_approval needs.
# --------------------------------------------------------------------------

def _set_path(doc: dict[str, Any], path: str, value: Any) -> None:
    """Real MongoDB treats a dotted $set/$unset key as a nested field path,
    not a literal key — record_checkpoint_node_completed relies on exactly
    this ("node_results.<node_id>") to update one node's result without
    clobbering the others."""
    parts = path.split(".")
    cursor = doc
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _unset_path(doc: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    cursor = doc
    for part in parts[:-1]:
        cursor = cursor.get(part, {})
    cursor.pop(parts[-1], None)


class _FakeCollection:
    def __init__(self) -> None:
        self._docs: dict[str, dict[str, Any]] = {}

    def _match(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self._docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def find_one(self, query, projection=None):
        doc = self._match(query)
        return copy.deepcopy(doc) if doc is not None else None

    async def insert_one(self, doc):
        _id = doc.get("_id") or str(uuid.uuid4())
        if _id in self._docs:
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError(f"duplicate key: {_id}")
        self._docs[_id] = copy.deepcopy({**doc, "_id": _id})
        return SimpleNamespace(inserted_id=_id)

    async def update_one(self, query, update, *, upsert=False):
        doc = self._match(query)
        matched = doc is not None
        if doc is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
            doc = {"_id": str(uuid.uuid4()), **dict(query)}
        else:
            doc = copy.deepcopy(doc)
        if not matched:
            for field, value in update.get("$setOnInsert", {}).items():
                _set_path(doc, field, value)
        for field, value in update.get("$set", {}).items():
            _set_path(doc, field, value)
        for field, amount in update.get("$inc", {}).items():
            doc[field] = doc.get(field, 0) + amount
        for field in update.get("$unset", {}):
            _unset_path(doc, field)
        for field, value in update.get("$push", {}).items():
            doc.setdefault(field, []).append(copy.deepcopy(value))
        for field, value in update.get("$addToSet", {}).items():
            items = doc.setdefault(field, [])
            if value not in items:
                items.append(copy.deepcopy(value))
        self._docs[doc["_id"]] = copy.deepcopy(doc)
        return SimpleNamespace(
            matched_count=1 if matched else 0,
            modified_count=1,
            upserted_id=None if matched else doc["_id"],
        )

    async def find_one_and_delete(self, query):
        doc = self._match(query)
        if doc is None:
            return None
        del self._docs[doc["_id"]]
        return copy.deepcopy(doc)

    async def create_index(self, *args, **kwargs):
        return None


class _FakeDB:
    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _FakeCollection()
        return self._collections[name]


CHILD_NAME = "__pytest_subprocess_child__"
CHILD_YAML = f"""
name: {CHILD_NAME}
version: "1.0"
inputs:
  customer_id:
    type: text
    required: true
nodes:
  - id: echo
    type: Echo
    config:
      template: "hello {{{{inputs.customer_id}}}}"
edges: []
entry: echo
exit: echo
output:
  include_input: false
  nodes:
    - node_id: echo
      flatten: true
"""

FAILING_CHILD_NAME = "__pytest_subprocess_failing_child__"
# No entry/exit and a node referencing a template path that can never
# resolve — fails at run_workflow's own preflight, inside the async task.
FAILING_CHILD_YAML = f"""
name: {FAILING_CHILD_NAME}
version: "1.0"
inputs: {{}}
nodes:
  - id: broken
    type: Echo
    config:
      template: "{{{{outputs.nonexistent.value}}}}"
edges: []
entry: broken
exit: broken
"""


@pytest.fixture
def child_workflow_file():
    path = Path("workflows") / f"{CHILD_NAME}.yaml"
    path.write_text(CHILD_YAML)
    try:
        yield CHILD_NAME
    finally:
        path.unlink(missing_ok=True)


@pytest.fixture
def failing_child_workflow_file():
    path = Path("workflows") / f"{FAILING_CHILD_NAME}.yaml"
    path.write_text(FAILING_CHILD_YAML)
    try:
        yield FAILING_CHILD_NAME
    finally:
        path.unlink(missing_ok=True)


def _parent_spec_yaml(
    child_name: str,
    *,
    node_id: str = "run_subprocess",
    inputs: dict[str, Any] | None = None,
) -> str:
    return yaml.safe_dump({
        "name": "parent",
        "version": "1.0",
        "inputs": {},
        "nodes": [
            {
                "id": node_id,
                "type": "SubprocessAgent",
                "config": {
                    "workflow": child_name,
                    "inputs": {"customer_id": "cust-1"} if inputs is None else inputs,
                    "result_from": "workflow_output",
                    "timeout_seconds": 60,
                },
            },
        ],
        "edges": [],
        "entry": node_id,
        "exit": node_id,
    })


def _find_checkpoint(db: _FakeDB, run_id: str, session_id: str) -> dict[str, Any] | None:
    return db["run_checkpoints"]._match({"run_id": run_id, "session_id": session_id})


async def _start_and_run(spec, yaml_text, *, db, services, run_id, session_id):
    """Mirrors what POST /workflows/run always does before executing: create
    the durable "running" record and checkpoint first (start_new_run_record),
    then run. run_workflow() itself never does this — a direct call to it
    (as every test here makes) skips it entirely unless the caller does, and
    without it there is no run_checkpoints document for record_checkpoint_paused
    or mark_checkpoint_status to ever update."""
    await start_new_run_record(
        db,
        run_id=run_id,
        session=session_id,
        spec=spec,
        workflow_yaml=yaml_text,
        inputs={},
        collection_id="default",
    )
    return await run_workflow(
        spec, {}, session_id=session_id, services=services, run_id=run_id,
    )


@pytest.mark.asyncio
class TestLaunchAndFullRoundTrip:
    async def test_launch_pauses_the_parent_and_records_the_correlation(
        self, child_workflow_file,
    ):
        run_id = f"parent-{uuid.uuid4()}"
        session_id = "user@example.com"
        db = _FakeDB()
        manager = BackgroundRunManager(redis=None)
        services = {"audit_db": db, "background_run_manager": manager}
        _PAUSED_GRAPHS.pop(run_id, None)

        yaml_text = _parent_spec_yaml(child_workflow_file)
        spec = load_workflow_from_string(yaml_text)
        result = await _start_and_run(
            spec, yaml_text, db=db, services=services,
            run_id=run_id, session_id=session_id,
        )

        assert result["status"] == "paused"
        checkpoint = _find_checkpoint(db, run_id, session_id)
        assert checkpoint["pause_kind"] == "subprocess"
        assert checkpoint["paused_node_id"] == "run_subprocess"

        launch = db["subprocess_launches"]._match({"parent_run_id": run_id})
        assert launch is not None
        assert launch["child_workflow"] == child_workflow_file
        assert launch["status"] == "pending"

        # Let the fire-and-forget child task actually finish.
        await asyncio.gather(*list(manager._tasks))

    async def test_the_childs_result_resumes_and_completes_the_parent(
        self, child_workflow_file,
    ):
        run_id = f"parent-{uuid.uuid4()}"
        session_id = "user@example.com"
        db = _FakeDB()
        manager = BackgroundRunManager(redis=None)
        services = {"audit_db": db, "background_run_manager": manager}
        _PAUSED_GRAPHS.pop(run_id, None)

        yaml_text = _parent_spec_yaml(child_workflow_file)
        spec = load_workflow_from_string(yaml_text)
        paused = await _start_and_run(
            spec, yaml_text, db=db, services=services,
            run_id=run_id, session_id=session_id,
        )
        assert paused["status"] == "paused"

        # The child launched via the exact same BackgroundRunManager path a
        # person-started run takes — waiting for it is waiting for a real
        # asyncio task, not driving anything by hand.
        await asyncio.gather(*list(manager._tasks))

        checkpoint = _find_checkpoint(db, run_id, session_id)
        assert checkpoint["status"] == "completed"
        node_result = checkpoint["node_results"]["run_subprocess"]["output"]
        assert node_result["status"] == "completed"
        assert node_result["result"] == {"text": "hello cust-1"}
        assert node_result["child_workflow"] == child_workflow_file
        assert node_result["child_run_id"]

        launch = db["subprocess_launches"]._match({"parent_run_id": run_id})
        assert launch["status"] == "delivered", "delivered once, never deleted"

    async def test_a_failed_child_fails_the_parent_instead_of_hanging_forever(
        self, failing_child_workflow_file,
    ):
        run_id = f"parent-{uuid.uuid4()}"
        session_id = "user@example.com"
        db = _FakeDB()
        manager = BackgroundRunManager(redis=None)
        services = {"audit_db": db, "background_run_manager": manager}
        _PAUSED_GRAPHS.pop(run_id, None)

        yaml_text = _parent_spec_yaml(failing_child_workflow_file, inputs={})
        spec = load_workflow_from_string(yaml_text)
        paused = await _start_and_run(
            spec, yaml_text, db=db, services=services,
            run_id=run_id, session_id=session_id,
        )
        assert paused["status"] == "paused"

        await asyncio.gather(*list(manager._tasks))

        checkpoint = _find_checkpoint(db, run_id, session_id)
        assert checkpoint["status"] == "failed"


@pytest.mark.asyncio
class TestIdempotentLaunch:
    async def test_a_replayed_launch_reuses_the_same_child_run_id(self, child_workflow_file):
        db = _FakeDB()
        calls: list[str] = []

        class _StubRunManager:
            def launch(self, coro, *, db, run_id, session, services=None, record_rejection_reason=False):
                calls.append(run_id)
                coro.close()  # never actually run the child in this test

        instance = SubprocessAgent("run_subprocess", {
            "workflow": child_workflow_file,
            "inputs": {"customer_id": "cust-1"},
        })
        instance.services = {"audit_db": db, "background_run_manager": _StubRunManager()}

        state = {
            "inputs": {"SYSTEM.run_id": "parent-1"},
            "session_id": "user@example.com",
            "node_outputs": {},
        }
        cfg = instance.config

        first = await instance._get_or_create_launch(cfg, state)
        second = await instance._get_or_create_launch(cfg, state)

        assert first["child_run_id"] == second["child_run_id"]
        assert calls == [first["child_run_id"]]  # launched exactly once


@pytest.mark.asyncio
class TestStandaloneCallbackEndpoint:
    """POST /workflows/subprocess-callback/{token} — the real, independently
    callable webhook (app.api.workflows.subprocess_callback), exercised
    directly rather than through the in-process finalize hook every other
    test in this file goes through, and with the child's own run replaced by
    a stub that never actually executes it — this isolates the endpoint's
    own token/delivery logic from the launch/run machinery already covered
    elsewhere in this file.
    """

    async def test_delivering_completes_the_paused_parent_and_a_repeat_call_is_a_noop(
        self, child_workflow_file,
    ):
        from fastapi import Request

        from app.api.workflows import SubprocessCallbackRequest, subprocess_callback

        run_id = f"parent-{uuid.uuid4()}"
        session_id = "user@example.com"
        db = _FakeDB()

        class _StubRunManager:
            def launch(self, coro, **kwargs):
                coro.close()  # the callback delivers the result by hand below

        services = {"audit_db": db, "background_run_manager": _StubRunManager()}
        _PAUSED_GRAPHS.pop(run_id, None)

        yaml_text = _parent_spec_yaml(child_workflow_file)
        spec = load_workflow_from_string(yaml_text)
        paused = await _start_and_run(
            spec, yaml_text, db=db, services=services,
            run_id=run_id, session_id=session_id,
        )
        assert paused["status"] == "paused"

        launch = db["subprocess_launches"]._match({"parent_run_id": run_id})
        token = launch["token"]
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=services)))

        first = await subprocess_callback(
            token,
            SubprocessCallbackRequest(status="completed", output={"text": "hi"}),
            request,  # type: ignore[arg-type]
        )
        assert first["status"] == "completed"
        checkpoint = _find_checkpoint(db, run_id, session_id)
        assert checkpoint["status"] == "completed"
        assert checkpoint["node_results"]["run_subprocess"]["output"]["result"] == {"text": "hi"}

        second = await subprocess_callback(
            token,
            SubprocessCallbackRequest(status="completed", output={"text": "hi"}),
            request,  # type: ignore[arg-type]
        )
        assert second["status"] == "noop"


@pytest.mark.asyncio
class TestDepthGuard:
    async def test_a_chain_at_the_configured_limit_is_refused_before_touching_the_ledger(
        self, child_workflow_file,
    ):
        db = _FakeDB()
        instance = SubprocessAgent("run_subprocess", {
            "workflow": child_workflow_file,
            "inputs": {"customer_id": "cust-1"},
        })
        instance.services = {
            "audit_db": db,
            "background_run_manager": BackgroundRunManager(redis=None),
            "subprocess_depth": settings.subprocess_max_depth,
        }
        state = {
            "inputs": {"SYSTEM.run_id": "parent-1"},
            "session_id": "user@example.com",
            "node_outputs": {},
        }
        with pytest.raises(RuntimeError, match="recursive"):
            await instance._get_or_create_launch(instance.config, state)

        assert db["subprocess_launches"]._docs == {}


class TestResolveChildInputs:
    def _child_spec(self, **inputs):
        from app.runtime.schema import WorkflowSpec

        return WorkflowSpec(
            name="child",
            version="1.0",
            inputs=inputs,
            nodes=[{"id": "n", "type": "Literal", "config": {"value": 1}}],
            entry="n",
            exit="n",
        )

    def test_explicit_mapping_wins(self):
        child_spec = self._child_spec(x=WorkflowInputSpec(type="text"))
        cfg = SimpleNamespace(inputs={"x": "explicit"})
        state = {"inputs": {"x": "from-parent-input"}, "node_outputs": {"x": {"value": "from-node"}}}
        assert _resolve_child_inputs(cfg, child_spec, state) == {"x": "explicit"}

    def test_falls_back_to_same_named_parent_input(self):
        child_spec = self._child_spec(x=WorkflowInputSpec(type="text"))
        cfg = SimpleNamespace(inputs={})
        state = {"inputs": {"x": "from-parent-input"}, "node_outputs": {}}
        assert _resolve_child_inputs(cfg, child_spec, state) == {"x": "from-parent-input"}

    def test_falls_back_to_same_named_parent_node_output(self):
        # json-typed, not text: a whole node's dict output is the realistic
        # case this fallback exists for, and _coerce_for_target only passes
        # a dict through unchanged for a json-typed target (a text-typed one
        # JSON-encodes it instead, which is its own, differently-tested
        # behavior).
        child_spec = self._child_spec(x=WorkflowInputSpec(type="json"))
        cfg = SimpleNamespace(inputs={})
        state = {"inputs": {}, "node_outputs": {"x": {"value": "node-output"}}}
        assert _resolve_child_inputs(cfg, child_spec, state) == {"x": {"value": "node-output"}}

    def test_none_when_nothing_resolves(self):
        child_spec = self._child_spec(x=WorkflowInputSpec(type="text"))
        cfg = SimpleNamespace(inputs={})
        state = {"inputs": {}, "node_outputs": {}}
        assert _resolve_child_inputs(cfg, child_spec, state) == {"x": None}

    def test_json_input_unwraps_the_raw_parsed_envelope(self):
        child_spec = self._child_spec(x=WorkflowInputSpec(type="json"))
        cfg = SimpleNamespace(inputs={"x": {"raw": "...", "parsed": {"a": 1}}})
        state = {"inputs": {}, "node_outputs": {}}
        assert _resolve_child_inputs(cfg, child_spec, state) == {"x": {"a": 1}}


class TestPreflightNeverBlowsTheStack:
    def test_a_long_subprocess_chain_is_validated_without_recursionerror(self, tmp_path):
        """Not a cycle — a legitimately long chain. Regardless, the field-index
        enrichment and the cycle check must both stay well within the
        interpreter's recursion limit; this is what a naive recursive design
        would have failed."""
        import sys

        chain_len = 40
        names = [f"__pytest_chain_{i}__" for i in range(chain_len)]
        try:
            for i, name in enumerate(names):
                nxt = names[i + 1] if i + 1 < len(names) else None
                nodes = [{"id": "seed", "type": "Literal", "config": {"value": 1}}]
                edges = []
                entry = exit_ = "seed"
                if nxt:
                    nodes.append({
                        "id": "call_next",
                        "type": "SubprocessAgent",
                        "config": {"workflow": nxt, "inputs": {}},
                    })
                    edges.append({"from": "seed", "to": "call_next"})
                    exit_ = "call_next"
                spec = {
                    "name": name, "version": "1.0", "inputs": {},
                    "nodes": nodes, "edges": edges, "entry": entry, "exit": exit_,
                }
                (Path("workflows") / f"{name}.yaml").write_text(yaml.safe_dump(spec))

            old_limit = sys.getrecursionlimit()
            sys.setrecursionlimit(150)
            try:
                report = preflight_workflow_yaml(
                    (Path("workflows") / f"{names[0]}.yaml").read_text()
                )
            finally:
                sys.setrecursionlimit(old_limit)

            assert "SUBPROCESS_RECURSION" not in {i.code for i in report.issues}
        finally:
            for name in names:
                (Path("workflows") / f"{name}.yaml").unlink(missing_ok=True)
