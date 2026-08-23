from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request

from app.api.workflows import (
    AutofixNodeRequest,
    _node_autofix_scope_valid,
    autofix_node,
)
from app.security.dependencies import CurrentUser
from app.security.rbac import Role


USER = CurrentUser("alice", Role.CONSULTANT, session_id="alice-scope")
BROKEN = """
name: Scoped repair
entry: first
exit: second
nodes:
  - id: first
    type: Literal
    config:
      value: hello
  - id: second
    type: Echo
    config:
      templat: '{{outputs.first.value}}'
edges:
  - from: first
    to: second
"""


def request(services=None) -> Request:
    return cast(Request, SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(services=services or {})),
    ))


def test_scope_accepts_only_selected_node_configuration_changes():
    changed_config = BROKEN.replace("templat:", "template:")
    assert _node_autofix_scope_valid(BROKEN, changed_config, "second")
    assert not _node_autofix_scope_valid(BROKEN, changed_config, "first")
    assert not _node_autofix_scope_valid(
        BROKEN, changed_config.replace("to: second", "to: first"), "second",
    )
    assert not _node_autofix_scope_valid(
        BROKEN, changed_config.replace("type: Echo", "type: Literal"), "second",
    )
    assert not _node_autofix_scope_valid(
        BROKEN, changed_config.replace("name: Scoped repair", "name: Different"), "second",
    )


@pytest.mark.asyncio
async def test_selected_node_autofix_repairs_config_typo_without_llm():
    result = await autofix_node(
        AutofixNodeRequest(workflow_yaml=BROKEN, node_id="second"),
        request(),
        USER,
    )
    assert result.changed is True
    assert result.fixed is True
    assert "template:" in result.yaml
    assert "templat:" not in result.yaml
    assert result.deterministic_fixes_applied
    assert result.llm_attempts == []
    assert result.preflight_report["valid"] is True


@pytest.mark.asyncio
async def test_valid_selected_node_is_noop_and_spends_no_tokens():
    valid = BROKEN.replace("templat:", "template:")
    result = await autofix_node(
        AutofixNodeRequest(workflow_yaml=valid, node_id="second"),
        request(),
        USER,
    )
    assert result.yaml == valid
    assert result.changed is False
    assert result.fixed is True
    assert result.llm_attempts == []