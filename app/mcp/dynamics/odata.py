"""Safe construction of Dataverse Web API queries.

The reference implementation builds filters like this:

    `api/data/v9.2/opportunities?$filter=_customerid_value eq ${accountId}`

`accountId` arrives from a tool call, which — in an agentic system — means it can
arrive from a language model reading a customer's email. Interpolated directly,
a value containing `or 1 eq 1` or a closing quote rewrites the query: at best it
returns records the caller should not see, at worst it changes what a subsequent
`$expand` traverses. It is the same class of bug as SQL injection, and it needs
the same discipline.

Two rules, applied without exception:

1.  **Identifiers are GUIDs.** Anything that goes into a key predicate or an
    `eq` against a lookup is validated as a GUID, not escaped. An identifier
    that is not a GUID is a caller error, not something to sanitise.
2.  **Free text is escaped and bounded.** Single quotes are doubled per the
    OData literal rules, control characters are stripped, and length is capped
    before the value reaches a query string.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

#: Dataverse accepts a GUID with or without braces; both normalise to the plain
#: 8-4-4-4-12 form.
_GUID = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

#: Beyond this, a "company name" is not a company name. Dataverse would reject
#: the URL anyway; failing here gives a better message and never builds it.
MAX_TERM_LENGTH = 200


class ODataValueError(ValueError):
    """A caller-supplied value cannot be used safely in a query."""


def guid(value: Any, *, field: str = "id") -> str:
    """Validate an identifier as a GUID and return its canonical form."""
    if not isinstance(value, str):
        raise ODataValueError(
            f"{field} must be a Dynamics GUID, got {type(value).__name__}"
        )
    candidate = value.strip()
    if not _GUID.fullmatch(candidate):
        raise ODataValueError(
            f"{field} must be a Dynamics record id (a GUID), got {candidate[:60]!r}. "
            "Map it from a previous CRM lookup rather than typing it."
        )
    return candidate.strip("{}").lower()


def escape_literal(value: Any, *, field: str = "value") -> str:
    """Escape a string for use inside an OData string literal.

    Single quotes are doubled — the OData v4 escape — and control characters are
    removed outright: a newline inside a `$filter` is never legitimate and is a
    reliable sign of an injection attempt or a mangled value.
    """
    if value is None:
        raise ODataValueError(f"{field} is required")
    if not isinstance(value, str):
        value = str(value)
    cleaned = _CONTROL_CHARS.sub("", value).strip()
    if not cleaned:
        raise ODataValueError(f"{field} cannot be empty")
    if len(cleaned) > MAX_TERM_LENGTH:
        raise ODataValueError(
            f"{field} is too long ({len(cleaned)} characters, "
            f"maximum {MAX_TERM_LENGTH})"
        )
    return cleaned.replace("'", "''")


def string_filter(field: str, value: str) -> str:
    """`field eq 'value'` with the value escaped."""
    return f"{field} eq '{escape_literal(value, field=field)}'"


def contains_filter(field: str, value: str) -> str:
    """`contains(field,'value')` with the value escaped."""
    return f"contains({field},'{escape_literal(value, field=field)}')"


def lookup_filter(field: str, record_id: str) -> str:
    """`_lookup_value eq <guid>` — the case the reference implementation gets
    wrong. A lookup comparison takes a bare GUID, not a quoted string, so
    validation (not escaping) is what makes it safe."""
    return f"{field} eq {guid(record_id, field=field)}"


def any_of(*filters: str) -> str:
    """OR the given filters, parenthesised so precedence cannot surprise."""
    usable = [item for item in filters if item]
    if not usable:
        raise ODataValueError("at least one filter is required")
    if len(usable) == 1:
        return usable[0]
    return " or ".join(f"({item})" for item in usable)


def all_of(*filters: str) -> str:
    usable = [item for item in filters if item]
    if not usable:
        raise ODataValueError("at least one filter is required")
    if len(usable) == 1:
        return usable[0]
    return " and ".join(f"({item})" for item in usable)


def entity_path(entity_set: str, record_id: str | None = None) -> str:
    """Build an entity path, validating the key predicate.

    `entity_set` is never caller-supplied — it comes from this package's own
    tool definitions — but it is checked anyway so a future refactor cannot
    quietly make it dynamic.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", entity_set):
        raise ODataValueError(f"invalid entity set {entity_set!r}")
    if record_id is None:
        return entity_set
    return f"{entity_set}({guid(record_id, field=f'{entity_set} id')})"


def build_query(
    *,
    select: list[str] | None = None,
    filter_expression: str | None = None,
    order_by: str | None = None,
    top: int | None = None,
    expand: str | None = None,
) -> dict[str, str]:
    """Assemble OData system query options.

    `$select` is required in practice on every read this package performs: the
    reference implementation omits it, so `fetch-accounts` returns every column
    of every account — hundreds of fields per record, including ones nobody
    intended to expose to a model.
    """
    params: dict[str, str] = {}
    if select:
        params["$select"] = ",".join(_column(name) for name in select)
    if filter_expression:
        params["$filter"] = filter_expression
    if order_by:
        params["$orderby"] = _order_by(order_by)
    if top is not None:
        if not isinstance(top, int) or top < 1 or top > 200:
            raise ODataValueError("top must be an integer between 1 and 200")
        params["$top"] = str(top)
    if expand:
        params["$expand"] = expand
    return params


_COLUMN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")


def _column(name: str) -> str:
    if not _COLUMN.fullmatch(name):
        raise ODataValueError(f"invalid column name {name!r}")
    return name


def _order_by(clause: str) -> str:
    parts = clause.split()
    if not parts or len(parts) > 2:
        raise ODataValueError(f"invalid order by clause {clause!r}")
    _column(parts[0])
    if len(parts) == 2 and parts[1].lower() not in ("asc", "desc"):
        raise ODataValueError(f"invalid sort direction {parts[1]!r}")
    return clause


def encode_params(params: dict[str, str]) -> str:
    """Percent-encode query values while leaving OData's own syntax readable.

    `$filter` expressions contain spaces, quotes, commas and parentheses that
    are legal in a query string; over-encoding them produces filters Dataverse
    rejects, and under-encoding produces filters that break on the first `&` in
    a company name.
    """
    safe_characters = "()',$ /="
    return "&".join(
        f"{key}={quote(value, safe=safe_characters)}"
        for key, value in params.items()
    )
