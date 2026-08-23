"""Resolve {{node_id.field.subfield}} expressions inside node configs.

Resolution happens at runtime, not at compile time — the values being
referenced don't exist until upstream nodes have executed."""
from __future__ import annotations

import re
from types import UnionType
from typing import Any, Union, get_args, get_origin

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


# ---------------------------------------------------------------------------
# post-resolution cleanup
# ---------------------------------------------------------------------------
#
# Whole-value mode deliberately preserves types, which means a reference to an
# optional value that was never supplied substitutes None rather than "". For a
# config field typed `Any` that is exactly right — MCPToolAgent reads None as
# "there was nothing to look up" and skips the call. For a field typed `str` it
# is not: `context: {Subject: '{{inputs.subject}}'}` with no subject given blew
# up as a raw pydantic `Input should be a valid string` error, naming a config
# key the person running the workflow never typed and no node they can see.
#
# An absent optional value is not a malformed config — it is the field not
# having been supplied. So it is dropped, and the field's own default applies,
# exactly as if the author had left it out. Only fields that HAVE a default are
# dropped: a required field resolving to None is a real problem and still
# surfaces as a validation error rather than silently becoming something else.

_UNKNOWN = object()


def _union_members(annotation: Any) -> list[Any]:
    """Internal helper for the union members step.

    Args:
        annotation (Any): The annotation.

    Returns:
        list[Any]: The members.
    """
    if get_origin(annotation) in (Union, UnionType):
        return list(get_args(annotation))
    return [annotation]


def _accepts_none(annotation: Any) -> bool:
    """Whether a value of None satisfies this annotation."""
    if annotation is None or annotation is Any or annotation is type(None):
        return True
    if get_origin(annotation) in (Union, UnionType):
        return any(_accepts_none(member) for member in get_args(annotation))
    return False


def _element_annotation(annotation: Any, container: type, position: int) -> Any:
    """The declared type of a dict value / list item, or _UNKNOWN.

    Reads through a union, so `dict[str, str] | None` still reports `str` for
    its values.
    """
    for member in _union_members(annotation):
        if get_origin(member) is container:
            args = get_args(member)
            if len(args) > position:
                return args[position]
            return Any
    return _UNKNOWN


def prune_absent(config: dict[str, Any], schema: Any) -> dict[str, Any]:
    """Drop config values that resolved to None but cannot be None.

    Applied to a node's resolved config before the node validates it. Returns a
    new dict; the input is not modified.
    """
    fields = getattr(schema, "model_fields", None)
    if not fields:
        return config

    cleaned: dict[str, Any] = {}
    for key, value in config.items():
        field = fields.get(key)
        if field is None:
            cleaned[key] = value
            continue

        annotation = field.annotation
        if value is None:
            # Required-and-None stays put so validation reports it. Everything
            # else falls back to the field's declared default.
            if _accepts_none(annotation) or field.is_required():
                cleaned[key] = value
            continue

        if isinstance(value, dict):
            element = _element_annotation(annotation, dict, 1)
            if element is not _UNKNOWN and not _accepts_none(element):
                value = {k: v for k, v in value.items() if v is not None}
        elif isinstance(value, list):
            element = _element_annotation(annotation, list, 0)
            if element is not _UNKNOWN and not _accepts_none(element):
                value = [item for item in value if item is not None]

        cleaned[key] = value
    return cleaned