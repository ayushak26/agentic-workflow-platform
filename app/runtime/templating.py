"""Resolve {{node_id.field.subfield}} expressions inside node configs.

Resolution happens at runtime, not at compile time — the values being
referenced don't exist until upstream nodes have executed."""
from __future__ import annotations
import re
from typing import Any

TEMPLATE_RE = re.compile(r"\{\{\s*([\w\.]+)\s*\}\}")


def _lookup(path: str, state: dict) -> Any:
    """Walk a dotted path against a dict. Supports 'inputs.foo' and
    'node_id.field' — the latter is sugar for 'node_outputs.node_id.field'."""
    parts = path.split(".")
    # Sugar: if the first segment matches a node_outputs key, prepend.
    node_outputs = state.get("node_outputs", {})
    if parts[0] in node_outputs:
        parts = ["node_outputs"] + parts
    cursor: Any = state
    for p in parts:
        if isinstance(cursor, dict) and p in cursor:
            cursor = cursor[p]
        else:
            raise KeyError(f"Template path not resolvable: {path}")
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
        return TEMPLATE_RE.sub(lambda mm: str(_lookup(mm.group(1), state)), value)
    if isinstance(value, dict):
        return {k: resolve(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, state) for v in value]
    return value