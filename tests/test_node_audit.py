from __future__ import annotations

import re
from pathlib import Path

import app.nodes  # noqa: F401
from app.nodes.registry import NodeRegistry


AUDIT = Path(__file__).parents[1] / "docs" / "WORKFLOW_NODE_AUDIT.md"


def test_audit_accounts_for_every_registered_node_exactly_once():
    rows = re.findall(r"^\| ([A-Za-z][A-Za-z0-9]+) \|", AUDIT.read_text(), re.MULTILINE)
    audited = [name for name in rows if name in NodeRegistry._registry]

    assert sorted(audited) == sorted(NodeRegistry._registry)
    assert len(audited) == len(set(audited))


def test_audit_uses_only_supported_decisions():
    text = AUDIT.read_text()
    rows = [line for line in text.splitlines() if line.startswith("|")]
    node_rows = [line for line in rows if any(f"| {name} |" in line for name in NodeRegistry._registry)]
    allowed = {"Keep", "Edit", "Merge", "Preset", "Deprecate", "New"}

    for row in node_rows:
        columns = [column.strip() for column in row.strip("|").split("|")]
        assert columns[3] in allowed, row