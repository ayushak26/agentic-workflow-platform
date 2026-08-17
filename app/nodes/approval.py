"""Shared "did a human already approve this?" check.

Extracted from `MCPToolAgent` (which introduced it first) so every node type
that gates a write behind human review — MCP tools, External Action, and
whatever comes next — reads the run's own state through one implementation
rather than three copies that can drift.
"""
from __future__ import annotations

from typing import Any


def human_approved(state: dict[str, Any]) -> bool:
    """Did a human review approve something earlier on this run's path?

    Read from completed node outputs, so it reflects what actually happened
    rather than what the graph promises. A rejection never counts as approval.
    """
    for output in (state.get("node_outputs") or {}).values():
        if isinstance(output, dict) and output.get("decision") in ("approve", "edit"):
            return True
    return False
