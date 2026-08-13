"""Resolve {{node_id.field.subfield}} expressions inside node configs.

Resolution happens at runtime, not at compile time — the values being
referenced don't exist until upstream nodes have executed."""
from __future__ import annotations

import re
from typing import Any

#: A trailing `?` marks the reference as OPTIONAL:
#:
#:     {{outputs.find_account.first.account_id}}    required — missing is an error
#:     {{outputs.find_account.first.account_id?}}   optional — missing is None
#:
#: Required is the right default: a template that silently resolves to nothing
#: is how a workflow ends up sending a customer an email addressed to "None".
#: But some references are legitimately absent — a CRM lookup that found no
#: account has no account id, and that is a business outcome rather than a
#: fault. Before this existed the only way to express it was to not reference
#: the value at all.
#:
#: The `?` sits outside the capture group, so every existing consumer of
#: TEMPLATE_RE (preflight's reference validation, the Builder's rename pass)
#: keeps seeing exactly the dotted path.
TEMPLATE_RE = re.compile(r"\{\{\s*([\w\.]+)\s*(\?)?\s*\}\}")


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

        # Numeric index into a list: `{{outputs.find_account.data.accounts.0.id}}`.
        # Integration results are list-shaped far more often than node outputs
        # are — a CRM search returns matches, not a record — and without this an
        # author has to add a Transform step purely to reach the first one.
        # Out-of-range is a miss, not a crash-with-a-different-message: it falls
        # through to the same self-diagnosing error below, which reports how many
        # items were actually there.
        if isinstance(cursor, list) and p.lstrip("-").isdigit():
            index = int(p)
            if -len(cursor) <= index < len(cursor):
                cursor = cursor[index]
                walked.append(p)
                continue

        # Build a precise, self-diagnosing failure. Show exactly which
        # segment failed and what keys WERE available at that level, so the
        # cause (missing node vs. wrong shape) is visible from the log alone.
        where = ".".join(walked) or "<root>"
        if isinstance(cursor, dict):
            available = sorted(cursor.keys())
        elif isinstance(cursor, list):
            available = (
                f"<list of {len(cursor)} item(s); use a numeric index such as "
                f"{where}.0>"
            )
        else:
            available = f"<not a dict: {type(cursor).__name__}={cursor!r}>"
        raise KeyError(
            f"Template path not resolvable: {path} — "
            f"failed at segment '{p}' under '{where}'; "
            f"available at that level: {available}"
        )
    return cursor


def _resolve_reference(match: "re.Match[str]", state: dict) -> Any:
    """Resolve one matched reference, honouring the optional marker."""
    path, optional = match.group(1), bool(match.group(2))
    if not optional:
        return _lookup(path, state)
    try:
        return _lookup(path, state)
    except KeyError:
        return None


def _as_text(value: Any) -> str:
    """Render a substituted value inside a larger string.

    None becomes empty rather than "None": an optional reference that did not
    resolve should leave a gap, not print the word.
    """
    return "" if value is None else str(value)


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
            return _resolve_reference(m, state)
        # Embedded mode: always returns a string. An absent optional reference
        # substitutes an empty string rather than the text "None".
        return TEMPLATE_RE.sub(
            lambda mm: _as_text(_resolve_reference(mm, state)), value
        )
    if isinstance(value, dict):
        return {k: resolve(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, state) for v in value]
    return value