"""Credential-aware launcher for the pinned paper-search-mcp server.

paper-search-mcp 0.1.4 does not send OpenAlex's ``api_key`` query parameter.
This launcher applies that parameter to the connector's requests.Session
before the upstream server creates its singleton searcher. The rest of the
MCP implementation remains upstream-owned.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any


def _prepare_source_checkout() -> None:
    value = os.getenv("PAPER_SEARCH_MCP_SOURCE_PATH", "").strip()
    if not value:
        return
    path = Path(value).expanduser().resolve()
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def inject_openalex_api_key(searcher: Any, api_key: str) -> bool:
    """Inject the key into a searcher session without logging the secret."""

    value = api_key.strip()
    session = getattr(searcher, "session", None)
    params = getattr(session, "params", None)
    if not value or params is None:
        return False
    params.update({"api_key": value})
    return True


def main() -> None:
    _prepare_source_checkout()
    module_name = (
        os.getenv("PAPER_SEARCH_MCP_MODULE", "").strip()
        or "paper_search_mcp.server"
    )
    upstream = importlib.import_module(module_name)

    key = (
        os.getenv("PAPER_SEARCH_MCP_OPENALEX_API_KEY", "")
        or os.getenv("OPENALEX_API_KEY", "")
    )
    inject_openalex_api_key(upstream.openalex_searcher, key)
    upstream.main()


if __name__ == "__main__":
    main()
