from __future__ import annotations

from copy import deepcopy

import pytest

from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import CallRequirement, Outcome
from app.proposal_graph.workspace_store import ProposalWorkspaceStore


def _matches(document, query):
    return all(document.get(key) == value for key, value in query.items())


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, spec, direction=None):
        pairs = spec if isinstance(spec, list) else [(spec, direction)]
        for key, order in reversed(pairs):
            self.documents.sort(
                key=lambda item: item.get(key),
                reverse=order == -1,
            )
        return self

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class Collection:
    def __init__(self):
        self.documents = []

    async def create_index(self, *args, **kwargs):
        return "index"

    async def find_one(self, query, projection=None, sort=None):
        found = [item for item in self.documents if _matches(item, query)]
        if sort:
            for key, order in reversed(sort):
                found.sort(
                    key=lambda item: item.get(key),
                    reverse=order == -1,
                )
        return deepcopy(found[0]) if found else None

    async def insert_one(self, document):
        self.documents.append(deepcopy(document))
        return object()

    def find(self, query, projection=None):
        documents = []
        for item in self.documents:
            if not _matches(item, query):
                continue
            copy = deepcopy(item)
            if projection:
                for key, include in projection.items():
                    if include == 0:
                        copy.pop(key, None)
            documents.append(copy)
        return Cursor(documents)

    async def find_one_and_update(
        self,
        query,
        update,
        return_document=None,
    ):
        for item in self.documents:
            if _matches(item, query):
                item.update(update.get("$set", {}))
                return deepcopy(item)
        return None


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        self.collections.setdefault(name, Collection())
        return self.collections[name]


class ObjectStore:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, data, key, content_type=None):
        self.objects[key] = data
        return key

    def get_bytes(self, key):
        return self.objects[key]


@pytest.mark.asyncio
async def test_source_versions_are_immutable_and_identical_uploads_deduplicate():
    store = ProposalWorkspaceStore(DB(), ObjectStore())
    common = {
        "session_id": "user@example.com",
        "proposal_id": "proposal-1",
        "source_id": "SRC-1",
        "title": "Evidence paper",
        "created_by": "user@example.com",
    }
    first = await store.register_source_version(
        content="Version one.",
        **common,
    )
    duplicate = await store.register_source_version(
        content="Version one.",
        **common,
    )
    second = await store.register_source_version(
        content="Version two.",
        **common,
    )

    assert first.version == duplicate.version == 1
    assert second.version == 2
    assert first.content_sha256 != second.content_sha256
    _, text = await store.source_text(
        session_id=common["session_id"],
        proposal_id=common["proposal_id"],
        source_id=common["source_id"],
        version_id=first.version_id,
    )
    assert text == "Version one."


@pytest.mark.asyncio
async def test_approval_freezes_snapshot_and_can_be_decided_once():
    store = ProposalWorkspaceStore(DB(), ObjectStore())
    graph = ProposalGraph(
        call_requirements={
            "CR-1": CallRequirement(
                id="CR-1",
                text="Deliver an improved outcome.",
                kind="expected_outcome",
                addressed_by_section="2.1",
                addressed_by_ids=["OUT-1"],
            )
        },
        outcomes={
            "OUT-1": Outcome(
                id="OUT-1",
                text="Improved outcome.",
                call_requirement_id="CR-1",
            )
        },
    )
    approval = await store.request_approval(
        session_id="user@example.com",
        proposal_id="proposal-1",
        graph=graph,
        stage="call_coverage",
        requested_by="user@example.com",
    )
    assert approval.status == "pending"
    assert approval.snapshot_version == 1

    decided = await store.decide_approval(
        session_id="user@example.com",
        proposal_id="proposal-1",
        approval_id=approval.approval_id,
        decision="approved",
        decided_by="reviewer@example.com",
        comment="Coverage accepted.",
    )
    assert decided.status == "approved"
    with pytest.raises(KeyError):
        await store.decide_approval(
            session_id="user@example.com",
            proposal_id="proposal-1",
            approval_id=approval.approval_id,
            decision="rejected",
            decided_by="reviewer@example.com",
            comment=None,
        )
