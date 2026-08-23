from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from app.api.candidates import (
    _acquired_document_from_run,
    download_acquired_research_document,
)
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from tests.fake_mongo import InMemoryDB


ALICE = CurrentUser("alice", Role.CONSULTANT, session_id="alice-scope")
BOB = CurrentUser("bob", Role.CONSULTANT, session_id="bob-scope")


class Store:
    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        self.calls.append((key, expires_seconds))
        return f"https://objects.example/{key}?signed=true"


def run_document(key: str = "evidence/run/source/version.pdf"):
    return {
        "run_id": "run-1",
        "session_id": "alice-scope",
        "node_runs": {
            "acquire": {
                "type_name": "ResearchSourceAcquirer",
                "output": {"documents": [{
                    "document_id": "DOC-1",
                    "candidate_id": "CAND-1",
                    "title": "Research paper",
                    "canonical_url": "https://journal.example/paper",
                    "pdf_object_key": key,
                }]},
            },
        },
    }


def request(db, store) -> Request:
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services={
        "audit_db": db, "object_store": store,
    }))))


def test_document_lookup_only_accepts_acquisition_output():
    document = _acquired_document_from_run(run_document(), "DOC-1")
    assert document is not None
    assert document["candidate_id"] == "CAND-1"
    assert _acquired_document_from_run({"node_runs": {"x": {"type_name": "Other", "output": {"documents": [{"document_id": "DOC-1"}]}}}}, "DOC-1") is None


@pytest.mark.asyncio
async def test_download_requires_owned_run_and_recorded_pdf():
    db = InMemoryDB()
    store = Store()
    await db["run_history"].insert_one(run_document())
    response = await download_acquired_research_document(
        "run-1", "DOC-1", request(db, store), ALICE,
    )
    assert response.status_code == 307
    assert response.headers["location"].startswith("https://objects.example/evidence/")
    assert response.headers["cache-control"] == "private, no-store"
    assert store.calls == [("evidence/run/source/version.pdf", 600)]

    with pytest.raises(HTTPException) as cross_owner:
        await download_acquired_research_document(
            "run-1", "DOC-1", request(db, store), BOB,
        )
    assert cross_owner.value.status_code == 404

    with pytest.raises(HTTPException) as wrong_document:
        await download_acquired_research_document(
            "run-1", "DOC-other", request(db, store), ALICE,
        )
    assert wrong_document.value.status_code == 404


@pytest.mark.asyncio
async def test_non_pdf_acquisition_is_not_offered_as_pdf_download():
    db = InMemoryDB()
    store = Store()
    await db["run_history"].insert_one(run_document("evidence/run/source/version.html"))
    with pytest.raises(HTTPException) as exc_info:
        await download_acquired_research_document(
            "run-1", "DOC-1", request(db, store), ALICE,
        )
    assert exc_info.value.status_code == 404
    assert store.calls == []