"""Turn an MCP tool result into a typed workflow output.

The problem this solves: MCP servers commonly return `JSON.stringify(record)`
inside a text block. Downstream, that is a string — so a workflow author ends up
either parsing JSON in a Transform step or, worse, feeding a JSON string back
into a model and asking it to pick a field out. Both are ways of losing a
contract you already had.

    CallToolResult
          │
          ├─ structuredContent present?  ──▶ use it (the typed path)
          ├─ text that declares itself JSON? ──▶ parse, bounded and safe
          └─ otherwise                    ──▶ expose as text, honestly
          │
          ▼
    { data, text, is_structured, raw }

`raw` is kept for debugging but trimmed and never re-parsed, so a huge or hostile
payload cannot ride along into workflow state.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.observability.logging import get_logger

log = get_logger(__name__)

#: Nesting depth beyond which a parsed payload is rejected. A deeply recursive
#: JSON document is a denial-of-service shape, not a CRM record.
MAX_PARSE_DEPTH = 24


class MCPToolResult(BaseModel):
    """The normalised result every MCP tool call produces."""

    #: The typed payload, when there is one. Always an object: a bare list is
    #: wrapped as {"items": [...]} so downstream mapping paths are stable
    #: whatever the server returned.
    data: dict[str, Any] = Field(default_factory=dict)
    #: Human-readable text, when the server sent any.
    text: str = ""
    #: True when `data` came from structuredContent or a parsed JSON payload,
    #: rather than being empty. Shown in the Builder so an author can see
    #: whether they have fields to map or only prose.
    is_structured: bool = False
    #: How `data` was obtained — useful when a server's typing is inconsistent.
    source: str = "none"
    #: Trimmed original, for debugging.
    raw: str = ""
    #: True when the server flagged the call as an error.
    is_error: bool = False


def _depth(value: Any, current: int = 0) -> int:
    if current > MAX_PARSE_DEPTH:
        return current
    if isinstance(value, dict):
        return max(
            (_depth(item, current + 1) for item in value.values()),
            default=current,
        )
    if isinstance(value, list):
        return max((_depth(item, current + 1) for item in value), default=current)
    return current


def _as_object(value: Any) -> dict[str, Any] | None:
    """Normalise a parsed payload into an object, or reject it.

    A list becomes `{"items": [...], "count": n}` so that a tool returning a
    collection and a tool returning a record are mapped the same way — and so
    `outputs.find_account.data.count` exists without the author writing a
    Transform to compute it.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value, "count": len(value)}
    return None


def parse_json_text(text: str, *, max_bytes: int) -> tuple[dict[str, Any] | None, str]:
    """Parse a text payload that looks like JSON, safely.

    Returns (data, reason). Bounded by size and depth, and only attempted when
    the text actually starts like JSON — a prose response beginning "Sorry, I
    could not..." must not be run through a parser at all.
    """
    stripped = text.strip()
    if not stripped:
        return None, "empty"
    if stripped[0] not in "{[":
        return None, "not_json"
    if len(stripped.encode("utf-8", errors="ignore")) > max_bytes:
        return None, "too_large"

    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None, "invalid_json"

    if _depth(parsed) > MAX_PARSE_DEPTH:
        return None, "too_deep"

    normalised = _as_object(parsed)
    if normalised is None:
        # A bare scalar is valid JSON but has no fields to map; exposing it as
        # {"value": …} would invent a key the server never declared.
        return None, "not_an_object"
    return normalised, "parsed"


def normalise_result(
    response: Any,
    *,
    max_bytes: int = 256_000,
    tool_name: str = "",
) -> MCPToolResult:
    """Normalise an MCP `CallToolResult` into a workflow output.

    Accepts the SDK's result object, and tolerates older/simpler shapes (a bare
    string, or an object with only `content`) so this works against servers that
    predate structured output.
    """
    if isinstance(response, str):
        data, reason = parse_json_text(response, max_bytes=max_bytes)
        return MCPToolResult(
            data=data or {},
            text=response if data is None else "",
            is_structured=data is not None,
            source="text_json" if data is not None else f"text:{reason}",
            raw=_trim(response, max_bytes),
        )

    is_error = bool(getattr(response, "isError", False))
    text = _collect_text(getattr(response, "content", None) or [])

    # 1. The typed path. A server that declares an outputSchema and returns
    # structuredContent has already done the work; trust it.
    structured = getattr(response, "structuredContent", None)
    normalised = _as_object(structured) if structured is not None else None
    if normalised is not None:
        return MCPToolResult(
            data=normalised,
            text=text,
            is_structured=True,
            source="structuredContent",
            raw=_trim(text, max_bytes),
            is_error=is_error,
        )

    # 2. The common reality: JSON serialised into a text block.
    data, reason = parse_json_text(text, max_bytes=max_bytes)
    if data is not None:
        log.debug(
            "mcp.result.parsed_json_text",
            tool=tool_name,
            keys=sorted(data)[:12],
        )
        return MCPToolResult(
            data=data,
            text="",
            is_structured=True,
            source="text_json",
            raw=_trim(text, max_bytes),
            is_error=is_error,
        )

    # 3. Genuinely unstructured. Say so rather than pretending otherwise — an
    # author seeing "no fields to map" will go and fix the tool's schema, which
    # is the right outcome.
    if reason not in ("not_json", "empty"):
        log.warning(
            "mcp.result.unparseable",
            tool=tool_name,
            reason=reason,
            length=len(text),
        )
    return MCPToolResult(
        data={},
        text=text,
        is_structured=False,
        source=f"text:{reason}",
        raw=_trim(text, max_bytes),
        is_error=is_error,
    )


def _collect_text(content: Any) -> str:
    parts: list[str] = []
    for block in content or []:
        value = getattr(block, "text", None)
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def _trim(text: str, max_bytes: int) -> str:
    if len(text) <= 4000:
        return text
    return text[:4000] + f"… (trimmed from {len(text)} characters)"


def field_paths_from_schema(
    schema: dict[str, Any] | None, prefix: str = "", depth: int = 0
) -> list[dict[str, Any]]:
    """Flatten a JSON Schema into typed dotted paths.

    Used for two things: the Builder's mapping picker (so an MCP tool's result
    is picked from a tree like any other node's output), and preflight, so
    `{{outputs.find_account.data.account_id}}` can be checked before a run when
    the server declares an outputSchema.
    """
    if not isinstance(schema, dict) or depth > 6:
        return []

    found: list[dict[str, Any]] = []
    properties = schema.get("properties")
    required = set(schema.get("required") or [])

    if isinstance(properties, dict):
        for name, child in properties.items():
            if not isinstance(child, dict):
                continue
            path = f"{prefix}.{name}" if prefix else name
            kind = _json_type(child)
            found.append(
                {
                    "path": path,
                    "type": kind,
                    "description": child.get("description", ""),
                    "required": name in required,
                    "enum_values": [
                        str(item) for item in (child.get("enum") or [])
                    ],
                }
            )
            if kind == "object":
                found.extend(field_paths_from_schema(child, path, depth + 1))
            elif kind == "list":
                items = child.get("items")
                if isinstance(items, dict) and items.get("type") == "object":
                    # Same `items` convention the visual schema builder uses for
                    # a list of objects, so both trees read identically.
                    found.extend(
                        field_paths_from_schema(items, f"{path}.items", depth + 1)
                    )
    return found


_JSON_TYPES = {
    "string": "string",
    "number": "number",
    "integer": "integer",
    "boolean": "boolean",
    "object": "object",
    "array": "list",
}


def _json_type(schema: dict[str, Any]) -> str:
    declared = schema.get("type")
    if isinstance(declared, list):
        # ["string", "null"] is how a nullable field is usually written.
        declared = next(
            (item for item in declared if item != "null"), None
        )
    if isinstance(declared, str):
        return _JSON_TYPES.get(declared, "unknown")
    if schema.get("enum"):
        return "enum"
    if schema.get("properties"):
        return "object"
    return "unknown"
