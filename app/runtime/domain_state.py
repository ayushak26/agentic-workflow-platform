"""Namespaced state for optional use-case packs.

The core runtime must not import proposal, healthcare, sales, or logistics
models. A use-case pack registers a reducer for its own namespace instead.
This keeps parallel LangGraph writes safe without coupling WorkflowState to a
single domain.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

DomainReducer = Callable[[Any, Any], Any]


def _merge_mapping(left: Any, right: Any) -> Any:
    """Safe default for simple dict-shaped domain state.

    Nested mappings are merged recursively. Other values use right-write-wins.
    Complex domains should register their own typed reducer.
    """

    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return right

    merged = dict(left)
    for key, value in right.items():
        if key in merged:
            merged[key] = _merge_mapping(merged[key], value)
        else:
            merged[key] = value
    return merged


class DomainStateRegistry:
    """Reducer registry keyed by a stable use-case namespace."""

    _reducers: dict[str, DomainReducer] = {}

    @classmethod
    def register(cls, namespace: str, reducer: DomainReducer) -> None:
        if not namespace or "." in namespace:
            raise ValueError(
                "domain namespace must be a non-empty identifier without dots"
            )
        existing = cls._reducers.get(namespace)
        if existing is not None and existing is not reducer:
            raise ValueError(f"domain reducer already registered: {namespace}")
        cls._reducers[namespace] = reducer

    @classmethod
    def reducer_for(cls, namespace: str) -> DomainReducer:
        return cls._reducers.get(namespace, _merge_mapping)


def merge_domain_state(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    """LangGraph reducer for state written by independent use-case packs."""

    merged = dict(left or {})
    for namespace, patch in (right or {}).items():
        if namespace in merged:
            merged[namespace] = DomainStateRegistry.reducer_for(namespace)(
                merged[namespace], patch
            )
        else:
            merged[namespace] = patch
    return merged