#!/usr/bin/env python3
"""Fail fast when the Studio frontend and FastAPI route table disagree."""
from __future__ import annotations

from pathlib import Path

import app.main


REQUIRED_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/workflows/validate"),
    ("POST", "/api/workflows/run"),
    ("GET", "/api/workflow-input-files/capabilities"),
    ("POST", "/api/workflow-input-files"),
    ("POST", "/api/workflow-input-files/extract"),
    ("GET", "/api/workflow-input-files/content"),
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
    return REQUIRED_ROUTES - registered_routes()


def main() -> int:
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
