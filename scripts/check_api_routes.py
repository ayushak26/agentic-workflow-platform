#!/usr/bin/env python3
"""Fail fast when the Studio frontend and FastAPI route table disagree."""
from __future__ import annotations

from pathlib import Path

import app.main


REQUIRED_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/workflows/validate"),
    ("POST", "/api/workflows/run"),
    ("POST", "/api/chat-conversations/resolve"),
    ("POST", "/api/chat-conversations/{conversation_id}/messages"),
    ("PUT", "/api/chat-conversations/{conversation_id}/messages/{message_id}"),
    ("GET", "/api/chat-workspace/experiences"),
    ("POST", "/api/chat-workspace/plan"),
    ("POST", "/api/chat-workspace/prepare"),
    ("GET", "/api/workflow-input-files/capabilities"),
    ("POST", "/api/workflow-input-files"),
    ("POST", "/api/workflow-input-files/extract"),
    ("GET", "/api/workflow-input-files/content"),
    ("GET", "/api/llm/models"),
    ("POST", "/api/llm/models/{model}/probe"),
    # Builder authoring surface: the schema builder, rule editor, mapping
    # picker, Test tab and Simulator each depend on one of these, so a UI
    # shipped without them looks broken in exactly the demo it was built for.
    ("GET", "/api/builder/operators"),
    ("POST", "/api/builder/output-contract"),
    ("POST", "/api/builder/schema-preview"),
    ("POST", "/api/builder/node-test"),
    ("POST", "/api/builder/simulate"),
    ("GET", "/api/builder/email/connections"),
    # MCP integration surface: the server picker, tool discovery and the tool
    # test panel are how a CRM capability is added to a workflow at all.
    ("GET", "/api/builder/mcp/servers"),
    ("GET", "/api/builder/mcp/servers/{server_id}/tools"),
    ("GET", "/api/builder/mcp/servers/{server_id}/health"),
    ("POST", "/api/builder/mcp/test-tool"),
}


def registered_routes() -> set[tuple[str, str]]:
    """Return every HTTP method/path pair mounted on the running app object."""

    result: set[tuple[str, str]] = set()
    # Recent FastAPI releases keep included routers as lazy
    # ``_IncludedRouter`` objects in app.routes instead of flattening every
    # APIRoute there. OpenAPI is built from the effective route table and is
    # therefore the stable public source for mounted HTTP operations.
    for path, operations in app.main.app.openapi().get("paths", {}).items():
        for method in operations:
            if method.upper() in {
                "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"
            }:
                result.add((method.upper(), path))
    return result


def missing_required_routes() -> set[tuple[str, str]]:
    """Compute the missing required routes.

    Returns:
        set[tuple[str, str]]: The required routes.
    """
    return REQUIRED_ROUTES - registered_routes()


def main() -> int:
    """Compute the main.

    Returns:
        int: The result.
    """
    loaded_from = Path(app.main.__file__).resolve()
    missing = missing_required_routes()

    print(f"FastAPI loaded from: {loaded_from}")
    if missing:
        print("Missing required routes:")
        for method, path in sorted(missing):
            print(f"  {method:6} {path}")
        print(
            "\nThe frontend and backend are from different versions, or "
            "app/main.py did not include the required routers."
        )
        return 1

    print("All required workflow validation and file-input routes are mounted.")
    for method, path in sorted(REQUIRED_ROUTES):
        print(f"  {method:6} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
