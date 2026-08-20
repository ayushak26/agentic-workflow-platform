"""Shared "did a human already approve this?" check.

Extracted from `MCPToolAgent` (which introduced it first) so every node type
that gates a write behind human review — MCP tools, External Action, and
whatever comes next — reads the run's own state through one implementation
rather than three copies that can drift.
"""
from __future__ import annotations

from typing import Any


def human_approved(state: dict[str, Any], approved_by: str | None = None) -> bool:
    """Did a human review approve something earlier on this run's path?

    Read from completed node outputs, so it reflects what actually happened
    rather than what the graph promises. A rejection never counts as approval.

    `approved_by` names the specific HumanInLoopAgent node this write is
    gated by. When given, only that node's decision counts — a workflow with
    more than one review on different branches must not let one branch's
    approval satisfy another branch's write. Left unset, this falls back to
    the old any-approval-anywhere scan, for callers that haven't named their
    reviewer yet.
    """
    node_outputs = state.get("node_outputs") or {}
    if approved_by is not None:
        output = node_outputs.get(approved_by)
        return isinstance(output, dict) and output.get("decision") in ("approve", "edit")
    for output in node_outputs.values():
        if isinstance(output, dict) and output.get("decision") in ("approve", "edit"):
            return True
    return False
