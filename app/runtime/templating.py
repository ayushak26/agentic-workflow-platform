"""Resolve {{node_id.field.subfield}} expressions inside node configs.

Resolution happens at runtime, not at compile time — the values being
referenced don't exist until upstream nodes have executed."""
from __future__ import annotations

import re
from typing import Any

TEMPLATE_RE = re.compile(r"\{\{\s*([\w\.]+)\s*\}\}")


def _lookup(path: str, state: dict) -> Any:
    """Walk a dotted path against workflow state.

    Stable public roots are ``inputs``, ``variables`` and ``outputs``.
    ``outputs`` is a virtual alias for the internal ``node_outputs`` map, so
    the state is not duplicated. The legacy ``node_id.field`` shorthand stays
    supported for existing workflows.
    """
    parts = path.split(".")
    if parts[0] == "outputs":
        parts[0] = "node_outputs"

    node_outputs = state.get("node_outputs", {})

    # Sugar: if the first segment matches a node_outputs key, prepend.
    if parts[0] in node_outputs:
        parts = ["node_outputs"] + parts

    cursor: Any = state
    walked: list[str] = []
    for p in parts:
        if isinstance(cursor, dict) and p in cursor:
            cursor = cursor[p]
            walked.append(p)
            continue

        # Build a precise, self-diagnosing failure. Show exactly which
        # segment failed and what keys WERE available at that level, so the
        # cause (missing node vs. wrong shape) is visible from the log alone.
        where = ".".join(walked) or "<root>"
        if isinstance(cursor, dict):
            available = sorted(cursor.keys())
        else:
            available = f"<not a dict: {type(cursor).__name__}={cursor!r}>"
        raise KeyError(
            f"Template path not resolvable: {path} — "
            f"failed at segment '{p}' under '{where}'; "
            f"available at that level: {available}"
        )
    return cursor


def resolve(value: Any, state: dict) -> Any:
    """Recursively walk a config value and replace template strings.

    Two substitution modes:
    - Whole-value: "{{node_id.field}}"  → returns the raw value (preserves type)
    - Embedded:    "Hello {{a.b}}"      → returns string with substitutions
    """
    if isinstance(value, str):
        # Whole-value mode: preserves non-string types
        m = TEMPLATE_RE.fullmatch(value.strip())
        if m:
            return _lookup(m.group(1), state)
        # Embedded mode: always returns a string
        return TEMPLATE_RE.sub(
            lambda mm: str(_lookup(mm.group(1), state)), value
        )
    if isinstance(value, dict):
        return {k: resolve(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, state) for v in value]
    return value