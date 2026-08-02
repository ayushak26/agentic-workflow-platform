"""A minimal in-memory Mongo stand-in with real read-after-write semantics.

Unlike the AsyncMock-based FakeDB used elsewhere (test_run_history.py,
test_durable_hitl.py) — which only records call args for one-shot
assertions — pipeline tests need a *stateful* store: stage 0 must actually
persist its output so stage 1's auto-match can read it back. Supports just
the operators this codebase's run_history/pipeline_history modules use:
$set, $setOnInsert, $addToSet, $pull, $inc, plus dotted-path filters and
updates (including into list-of-dict fields, e.g. "stages.0.run_id" or the
Mongo array-element-match "stages.run_id").
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


@dataclass
class _DeleteResult:
    deleted_count: int


def _matches_leaf(value: Any, expected: Any) -> bool:
    if isinstance(expected, dict) and ("$in" in expected or "$nin" in expected):
        if "$in" in expected:
            return value in expected["$in"]
        return value not in expected["$nin"]
    return value == expected


def _match_path(value: Any, parts: list[str], expected: Any) -> bool:
    if not parts:
        return _matches_leaf(value, expected)
    head, rest = parts[0], parts[1:]
    if isinstance(value, dict):
        return head in value and _match_path(value[head], rest, expected)
    if isinstance(value, list):
        if head.isdigit():
            idx = int(head)
            return idx < len(value) and _match_path(value[idx], rest, expected)
        # Mongo array-element-match: "stages.run_id" matches if ANY element
        # of the "stages" list has that run_id.
        return any(_match_path(item, parts, expected) for item in value)
    return False


def _set_path(container: Any, parts: list[str], value: Any, only_if_absent: bool = False) -> None:
    head, rest = parts[0], parts[1:]
    if not rest:
        if isinstance(container, list):
            idx = int(head)
            while len(container) <= idx:
                container.append(None)
            if only_if_absent and container[idx] is not None:
                return
            container[idx] = value
            return
        if only_if_absent and head in container:
            return
        container[head] = value
        return
    if head.isdigit() and isinstance(container, list):
        idx = int(head)
        while len(container) <= idx:
            container.append({})
        if container[idx] is None:
            container[idx] = {}
        _set_path(container[idx], rest, value, only_if_absent)
    else:
        nxt = container.setdefault(head, {})
        _set_path(nxt, rest, value, only_if_absent)


class InMemoryCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def _matches(self, doc: dict[str, Any], filter_: dict[str, Any]) -> bool:
        return all(
            _match_path(doc, key.split("."), expected)
            for key, expected in filter_.items()
        )

    async def create_index(self, *args, **kwargs) -> None:
        return None

    async def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs.append(copy.deepcopy(doc))

    async def find_one(
        self, filter_: dict[str, Any], projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        for doc in self.docs:
            if self._matches(doc, filter_):
                return copy.deepcopy(doc)
        return None

    def find(self, filter_: dict[str, Any], projection: dict[str, Any] | None = None):
        matched = [copy.deepcopy(d) for d in self.docs if self._matches(d, filter_)]
        return _Cursor(matched)

    def _apply_update(self, doc: dict[str, Any], update: dict[str, Any]) -> None:
        for key, value in (update.get("$setOnInsert") or {}).items():
            _set_path(doc, key.split("."), value, only_if_absent=True)
        for key, value in (update.get("$set") or {}).items():
            _set_path(doc, key.split("."), value)
        for key, value in (update.get("$addToSet") or {}).items():
            lst = doc.setdefault(key, [])
            if value not in lst:
                lst.append(value)
        for key, value in (update.get("$push") or {}).items():
            lst = doc.setdefault(key, [])
            if isinstance(value, dict) and "$each" in value:
                lst.extend(value["$each"])
            else:
                lst.append(value)
        for key, value in (update.get("$pull") or {}).items():
            doc[key] = [item for item in doc.get(key, []) if item != value]
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = doc.get(key, 0) + value
        for key in (update.get("$unset") or {}):
            doc.pop(key, None)

    async def update_one(
        self, filter_: dict[str, Any], update: dict[str, Any], upsert: bool = False,
    ) -> None:
        for doc in self.docs:
            if self._matches(doc, filter_):
                self._apply_update(doc, update)
                return
        if upsert:
            new_doc: dict[str, Any] = {}
            self._apply_update(new_doc, update)
            for key, value in filter_.items():
                if "." not in key and key not in new_doc:
                    new_doc[key] = value
            self.docs.append(new_doc)

    async def delete_one(self, filter_: dict[str, Any]) -> _DeleteResult:
        for i, doc in enumerate(self.docs):
            if self._matches(doc, filter_):
                del self.docs[i]
                return _DeleteResult(deleted_count=1)
        return _DeleteResult(deleted_count=0)


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, *args, **kwargs) -> "_Cursor":
        return self

    def limit(self, *args, **kwargs) -> "_Cursor":
        return self

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for doc in self._docs:
            yield doc


class InMemoryDB:
    def __init__(self) -> None:
        self.collections: dict[str, InMemoryCollection] = {}

    def __getitem__(self, name: str) -> InMemoryCollection:
        return self.collections.setdefault(name, InMemoryCollection())

    async def command(self, *args, **kwargs) -> dict[str, Any]:
        return {"ok": 1}
