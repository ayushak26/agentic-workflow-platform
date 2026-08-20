"""End-to-end coverage for workflows/w03_technical_service_case.yaml.

W03 was previously covered only by structural/preflight checks (see
test_workflow_examples_10_coverage.py) — nobody had actually run it. A live
investigation (chat message -> understand [subprocess: sp01] -> resolve
identity [subprocess: sp02] -> find equipment [MCP] -> service manual [RAG]
-> severity decision -> route) found the workflow itself runs correctly
end-to-end with realistic services; the real bug was in the client (the
"Run workflow" dialog never collecting a chat message for chatbot-mode Start
nodes — see RunDialog.tsx/RunDialog.test.tsx). This file locks in that the
workflow's own three severity branches (NORMAL_SERVICE, CRITICAL,
NEEDS_IDENTIFICATION) actually produce their documented output when a
message IS supplied, so a future change can't silently break any of them.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.runtime.executor import _PAUSED_GRAPHS, run_workflow
from app.runtime.hitl import resume_workflow_durable
from app.runtime.loader import load_workflow
from app.workflow.orchestration import BackgroundRunManager, start_new_run_record

WORKFLOW_PATH = Path("workflows/w03_technical_service_case.yaml")
WORKFLOW = load_workflow(WORKFLOW_PATH)

BASE_UNDERSTANDING = {
    "detected_language": "en",
    "translated_message": "My pump is making a loud noise. Order SO-45882.",
    "company_name": {"value": "Acme Pumps", "status": "KNOWN", "evidence": "stated"},
    "order_reference": {"value": "SO-45882", "status": "KNOWN", "evidence": "stated"},
    "requested_action": {"value": "fix the pump", "status": "INFERRED", "evidence": "implied"},
    "serial_number": {"value": "SN-1001", "status": "KNOWN", "evidence": "stated"},
    "urgency": {"value": "normal", "status": "KNOWN", "evidence": "stated"},
}


class StubLLM:
    def __init__(self, issue: str) -> None:
        self._issue = issue

    async def complete_structured(self, *, model, response_model, **_):
        parsed = {
            **BASE_UNDERSTANDING,
            "issue": {"value": self._issue, "status": "KNOWN", "evidence": "stated"},
        }
        return response_model.model_validate_json(json.dumps(parsed))

    async def complete(self, *, model, system, user, **_):
        return SimpleNamespace(text="The manual says to check the impeller. [1]")


class FakeRetrieval:
    async def __call__(self, query, *, llm):
        return SimpleNamespace(chunks=[], rewritten_query=None)


class FakePythonRunner:
    async def run(self, code, input_fields, *, timeout_seconds, memory_mb):
        namespace: dict[str, Any] = {"inputs": input_fields}
        exec(code, namespace)
        return {
            "status": "ok",
            "output": namespace.get("output", {}),
            "stdout": "",
            "stderr": "",
            "duration_s": 0.0,
        }


class FakeMCPService:
    def __init__(self, *, equipment_found: bool = True) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self._equipment_found = equipment_found

    async def call(self, *, server_id, tool_name, arguments, **kwargs):
        self.calls.append((server_id, tool_name, arguments))
        if tool_name == "find_customer":
            return _read_result(server_id, tool_name, {
                "customers": [{"account_id": "ACC-1", "account_name": "Acme Pumps", "key_account": False}],
                "count": 1,
            })
        if tool_name == "find_sales_order":
            return _read_result(server_id, tool_name, {"orders": [{"order_status": "open"}], "count": 1})
        if tool_name == "find_installed_unit":
            if not self._equipment_found:
                return _read_result(server_id, tool_name, {"units": [], "count": 0})
            return _read_result(server_id, tool_name, {
                "units": [{"pump_model": "XR-200", "warranty_active": True}], "count": 1,
            })
        raise AssertionError(f"unexpected tool call: {tool_name}")

    async def find_tool(self, server_id, tool_name):
        return None


def _read_result(server_id: str, tool_name: str, data: dict) -> dict:
    return {
        "server": server_id, "tool": tool_name, "operation": "read",
        "data": data, "text": "", "is_structured": True, "mode": "mock",
        "duration_s": 0.0, "deduplicated": False,
    }


class _FakeCollection:
    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}

    def _match(self, query):
        for doc in self._docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def find_one(self, query, projection=None):
        import copy
        doc = self._match(query)
        return copy.deepcopy(doc) if doc is not None else None

    async def insert_one(self, doc):
        import copy
        _id = doc.get("_id") or str(uuid.uuid4())
        self._docs[_id] = copy.deepcopy({**doc, "_id": _id})
        return SimpleNamespace(inserted_id=_id)

    async def update_one(self, query, update, *, upsert=False):
        import copy
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
        return SimpleNamespace(matched_count=1 if matched else 0, modified_count=1,
                                upserted_id=None if matched else doc["_id"])

    async def find_one_and_delete(self, query):
        import copy
        doc = self._match(query)
        if doc is None:
            return None
        del self._docs[doc["_id"]]
        return copy.deepcopy(doc)

    async def create_index(self, *args, **kwargs):
        return None


def _set_path(doc, path, value):
    parts = path.split(".")
    cursor = doc
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _unset_path(doc, path):
    parts = path.split(".")
    cursor = doc
    for part in parts[:-1]:
        cursor = cursor.get(part, {})
    cursor.pop(parts[-1], None)


class _FakeDB:
    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _FakeCollection()
        return self._collections[name]


def _services(*, issue: str, equipment_found: bool, db: _FakeDB, manager: BackgroundRunManager) -> dict:
    # No langgraph_checkpointer here deliberately: a subprocess pause resolves
    # itself via the delivery callback re-entering the in-memory _PAUSED_GRAPHS
    # graph directly, and adding a persistent checkpointer at this stage routes
    # that re-entry through the durable/Mongo-checkpoint path instead — which
    # this minimal fake DB doesn't populate enough to support (it fails with
    # `KeyError: 'workflow_yaml'`). A real HITL pause further down the same
    # run still resumes fine afterward purely via _PAUSED_GRAPHS (see
    # test_critical_case_pauses_for_review_then_escalates).
    return {
        "audit_db": db,
        "background_run_manager": manager,
        "llm": StubLLM(issue),
        "mcp": FakeMCPService(equipment_found=equipment_found),
        "retriever": FakeRetrieval(),
        "python_runner": FakePythonRunner(),
        "cost_ledger": SimpleNamespace(record=lambda *a, **k: None),
    }


async def _run_w03(*, message: str, issue: str, equipment_found: bool = True):
    run_id = f"w03-{uuid.uuid4()}"
    session_id = "user@example.com"
    db = _FakeDB()
    manager = BackgroundRunManager(redis=None)
    services = _services(issue=issue, equipment_found=equipment_found, db=db, manager=manager)
    _PAUSED_GRAPHS.pop(run_id, None)
    inputs = {"message": message}

    await start_new_run_record(
        db, run_id=run_id, session=session_id, spec=WORKFLOW,
        workflow_yaml=WORKFLOW_PATH.read_text(), inputs=inputs, collection_id="default",
    )
    result = await run_workflow(
        WORKFLOW, inputs, session_id=session_id, services=services, run_id=run_id,
        collection_id="default",
    )
    import asyncio
    while manager._tasks:
        await asyncio.gather(*list(manager._tasks))
    return result, db, run_id, services


def _run_history(db: _FakeDB, run_id: str) -> dict:
    return db["run_history"]._match({"run_id": run_id}) or {}


def _node_output(db: _FakeDB, run_id: str, node_id: str) -> dict:
    node_runs = _run_history(db, run_id).get("node_runs") or {}
    return (node_runs.get(node_id) or {}).get("output") or {}


@pytest.mark.asyncio
async def test_normal_service_case_replies_with_grounded_answer():
    result, db, run_id, _ = await _run_w03(
        message="My pump is making a loud noise. Order SO-45882.",
        issue="loud noise",
    )
    # The workflow's first pause is at the "understand" SubprocessAgent; it
    # resolves itself via the delivery callback once sp01 finishes, all the
    # way through to end_normal, without anything here driving a resume.
    assert result["status"] == "paused"
    history = _run_history(db, run_id)
    assert history.get("status") == "completed", history.get("error")
    assert _node_output(db, run_id, "assess_severity")["decisions"]["case_type"] == "NORMAL_SERVICE"
    answer = _node_output(db, run_id, "service_manual")["answer"]
    assert answer
    assert _node_output(db, run_id, "end_normal")["result"]["message"] == answer


@pytest.mark.asyncio
async def test_critical_case_pauses_for_review_then_escalates():
    result, db, run_id, services = await _run_w03(
        message="Production is completely stopped, pump is dead.",
        issue="production is completely stopped",
    )
    assert result["status"] == "paused"
    assert _run_history(db, run_id).get("pause_kind") == "hitl_gate"
    assert run_id in _PAUSED_GRAPHS

    resumed = await resume_workflow_durable(run_id, {"decision": "approve"}, services=services)
    assert resumed["status"] == "completed"
    outputs = resumed.get("state", {}).get("node_outputs", {})
    assert outputs["assess_severity"]["decisions"]["case_type"] == "CRITICAL"
    assert "end_critical" in outputs


@pytest.mark.asyncio
async def test_needs_identification_when_equipment_not_found():
    result, db, run_id, _ = await _run_w03(
        message="My pump is broken but I don't know the model.",
        issue="broken pump",
        equipment_found=False,
    )
    assert result["status"] == "paused"
    history = _run_history(db, run_id)
    assert history.get("status") == "completed", history.get("error")
    assert _node_output(db, run_id, "assess_severity")["decisions"]["case_type"] == "NEEDS_IDENTIFICATION"
    assert _node_output(db, run_id, "end_needs_identification")
