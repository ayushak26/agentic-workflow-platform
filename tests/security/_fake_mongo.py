"""Minimal in-memory Mongo test double for entity-vault tests.

Supports exactly the operations app/security/entity_vault.py uses:
find_one, insert_one (with _id uniqueness), find_one_and_update ($inc/$set,
upsert, return-after), find (with $in on one field), delete_one,
create_index (no-op). Not a general Mongo emulator.
"""
from __future__ import annotations

import copy
from typing import Any

from pymongo.errors import DuplicateKeyError


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return copy.deepcopy(self._docs[: length])

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for doc in copy.deepcopy(self._docs):
            yield doc


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict) and "$in" in expected:
            if doc.get(key) not in expected["$in"]:
                return False
        elif doc.get(key) != expected:
            return False
    return True


class FakeAsyncCollection:
    def __init__(self) -> None:
        self._docs: dict[Any, dict[str, Any]] = {}

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        if "_id" in query and len(query) == 1:
            doc = self._docs.get(query["_id"])
            return copy.deepcopy(doc) if doc is not None else None
        for doc in self._docs.values():
            if _matches(doc, query):
                return copy.deepcopy(doc)
        return None

    async def insert_one(self, doc: dict[str, Any]) -> Any:
        _id = doc["_id"]
        if _id in self._docs:
            raise DuplicateKeyError(f"duplicate key: {_id}")
        self._docs[_id] = copy.deepcopy(doc)

        class _Result:
            inserted_id = _id

        return _Result()

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
        return_document: Any = None,
    ) -> dict[str, Any] | None:
        _id = query.get("_id")
        doc = self._docs.get(_id)
        if doc is None:
            if not upsert:
                return None
            doc = {"_id": _id, **{k: v for k, v in query.items() if k != "_id"}}
        else:
            doc = copy.deepcopy(doc)
        for field, amount in update.get("$inc", {}).items():
            doc[field] = doc.get(field, 0) + amount
        for field, value in update.get("$set", {}).items():
            doc[field] = value
        self._docs[_id] = copy.deepcopy(doc)
        return copy.deepcopy(doc)

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        return _FakeCursor([d for d in self._docs.values() if _matches(d, query)])

    async def delete_one(self, query: dict[str, Any]) -> Any:
        _id = query.get("_id")
        deleted = 0
        if _id is not None and _id in self._docs:
            del self._docs[_id]
            deleted = 1
        else:
            for key, doc in list(self._docs.items()):
                if _matches(doc, query):
                    del self._docs[key]
                    deleted = 1
                    break

        class _Result:
            deleted_count = deleted

        return _Result()

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> Any:
        _id = query.get("_id")
        doc = self._docs.get(_id)
        matched = doc is not None
        if doc is None:
            if not upsert:
                class _NoMatch:
                    matched_count = 0
                    modified_count = 0
                    upserted_id = None

                return _NoMatch()
            doc = {"_id": _id, **{k: v for k, v in query.items() if k != "_id"}}
        else:
            doc = copy.deepcopy(doc)

        if not matched:
            for field, value in update.get("$setOnInsert", {}).items():
                doc[field] = value
        for field, value in update.get("$set", {}).items():
            doc[field] = value
        for field, amount in update.get("$inc", {}).items():
            doc[field] = doc.get(field, 0) + amount
        self._docs[_id] = copy.deepcopy(doc)

        class _Result:
            matched_count = 1 if matched else 0
            modified_count = 1
            upserted_id = None if matched else _id

        return _Result()

    async def find_one_and_delete(self, query: dict[str, Any]) -> dict[str, Any] | None:
        _id = query.get("_id")
        doc = self._docs.get(_id) if _id is not None else None
        if doc is None:
            for key, candidate in list(self._docs.items()):
                if _matches(candidate, query):
                    doc = candidate
                    del self._docs[key]
                    return copy.deepcopy(doc)
            return None
        del self._docs[_id]
        return copy.deepcopy(doc)

    async def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeAsyncDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeAsyncCollection] = {}

    def __getitem__(self, name: str) -> FakeAsyncCollection:
        if name not in self._collections:
            self._collections[name] = FakeAsyncCollection()
        return self._collections[name]
