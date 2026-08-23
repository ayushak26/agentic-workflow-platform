from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request

import app.api.chat_workflows as api
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from app.workflow.chat_workflow_store import ChatWorkflowStore
from tests.fake_mongo import InMemoryDB


VALID_YAML = """
name: Private Chat Test
description: A private workflow.
version: '1.0'
library:
  title: Private Chat Test
  visibility_status: approved
nodes:
  - id: echo
    type: Literal
    config:
      value: hello
edges: []
entry: echo
exit: echo
"""

ALICE = CurrentUser("alice", Role.CONSULTANT, session_id="alice-scope")
BOB = CurrentUser("bob", Role.CONSULTANT, session_id="bob-scope")


def request(db: InMemoryDB) -> Request:
    return cast(Request, SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(services={"audit_db": db})),
    ))


@pytest.mark.asyncio
async def test_import_is_private_owner_scoped_and_forces_draft_visibility():
    db = InMemoryDB()
    imported = await api.import_private_chat_workflow(
        api.ImportChatWorkflowRequest(
            slug="my_chat", display_name="My Chat", yaml=VALID_YAML,
        ),
        request(db),
        ALICE,
    )

    assert imported["visibility"] == "private"
    assert imported["source"] == "imported"
    alice_list = await api.list_private_chat_workflows(request(db), ALICE)
    bob_list = await api.list_private_chat_workflows(request(db), BOB)
    assert [item["id"] for item in alice_list["workflows"]] == [imported["id"]]
    assert bob_list["workflows"] == []

    detail = await api.get_private_chat_workflow(imported["id"], request(db), ALICE)
    assert "visibility_status: draft" in detail["yaml"]
    assert "visibility_status: approved" not in detail["yaml"]

    with pytest.raises(HTTPException) as exc_info:
        await api.get_private_chat_workflow(imported["id"], request(db), BOB)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_slug_is_per_owner():
    db = InMemoryDB()
    body = api.ImportChatWorkflowRequest(
        slug="same", display_name="Same", yaml=VALID_YAML,
    )
    await api.import_private_chat_workflow(body, request(db), ALICE)
    with pytest.raises(HTTPException) as exc_info:
        await api.import_private_chat_workflow(body, request(db), ALICE)
    assert exc_info.value.status_code == 409

    # Another owner may use the same friendly slug safely.
    bob = await api.import_private_chat_workflow(body, request(db), BOB)
    assert bob["slug"] == "same"


@pytest.mark.asyncio
async def test_deep_research_preset_is_idempotent_and_owner_scoped():
    db = InMemoryDB()
    alice_first = await api.ensure_deep_research_chat_workflow(request(db), ALICE)
    alice_second = await api.ensure_deep_research_chat_workflow(request(db), ALICE)
    bob = await api.ensure_deep_research_chat_workflow(request(db), BOB)

    assert alice_first["id"] == alice_second["id"]
    assert bob["id"] != alice_first["id"]
    assert alice_first["slug"] == "deep-research-chat"
    detail = await api.get_private_chat_workflow(alice_first["id"], request(db), ALICE)
    assert "BoundedDeepResearchAgent" in detail["yaml"]
    assert "ResearchSourceAcquirer" in detail["yaml"]
    assert "visibility_status: draft" in detail["yaml"]


@pytest.mark.asyncio
async def test_copy_existing_never_changes_or_creates_a_global_workflow(tmp_path, monkeypatch):
    db = InMemoryDB()
    source = tmp_path / "shared.yaml"
    source.write_text(VALID_YAML, encoding="utf-8")
    monkeypatch.setattr(api, "WORKFLOWS_DIR", tmp_path)

    copied = await api.copy_existing_chat_workflow(
        api.CopyChatWorkflowRequest(
            workflow_name="shared", slug="private_copy", display_name="Private copy",
        ),
        request(db),
        ALICE,
    )

    assert copied["source"] == "existing"
    assert copied["source_workflow_name"] == "shared"
    assert source.read_text(encoding="utf-8") == VALID_YAML
    assert sorted(path.name for path in tmp_path.glob("*.yaml")) == ["shared.yaml"]


@pytest.mark.asyncio
async def test_archive_and_publication_request_are_owner_scoped_status_changes_only():
    db = InMemoryDB()
    record = await ChatWorkflowStore(db).create(
        owner_scope_id="alice-scope",
        slug="publish_me",
        display_name="Publish me",
        description="",
        yaml_text=VALID_YAML,
        source="imported",
    )

    published = await api.request_private_workflow_publication(
        record.chat_workflow_id, request(db), ALICE,
    )
    assert published["status"] == "publish_requested"
    assert list(db["chat_workflows"].docs)[0]["yaml"] == VALID_YAML

    with pytest.raises(HTTPException) as exc_info:
        await api.archive_private_chat_workflow(
            record.chat_workflow_id, request(db), BOB,
        )
    assert exc_info.value.status_code == 404

    archived = await api.archive_private_chat_workflow(
        record.chat_workflow_id, request(db), ALICE,
    )
    assert archived["archived"] is True
    assert (await api.list_private_chat_workflows(request(db), ALICE))["workflows"] == []


@pytest.mark.asyncio
async def test_legacy_private_record_gets_safe_output_compatibility_shape():
    db = InMemoryDB()
    db["chat_workflows"].docs.append({
        "chat_workflow_id": "legacy",
        "owner_scope_id": "alice-scope",
        "slug": "legacy",
        "display_name": "Legacy",
        "description": "",
        "yaml": VALID_YAML,
        "source": "imported",
        "status": "private",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    })
    result = await api.list_private_chat_workflows(request(db), ALICE)
    compatibility = result["workflows"][0]["output_compatibility"]
    assert compatibility["supported"] is True
    assert compatibility["detected_types"] == ["text"]
    assert compatibility["warnings"]


@pytest.mark.asyncio
async def test_invalid_yaml_is_never_persisted():
    db = InMemoryDB()
    with pytest.raises(HTTPException) as exc_info:
        await api.import_private_chat_workflow(
            api.ImportChatWorkflowRequest(
                slug="broken", display_name="Broken", yaml="not: [valid",
            ),
            request(db),
            ALICE,
        )
    assert exc_info.value.status_code == 422
    assert db["chat_workflows"].docs == []


@pytest.mark.asyncio
async def test_workflow_without_visible_output_is_never_persisted():
    db = InMemoryDB()
    empty = """
name: Empty Chat
nodes:
  - id: end
    type: EndAgent
    config:
      mode: chat_response
      chat_message: ''
edges: []
entry: end
exit: end
"""
    with pytest.raises(HTTPException) as exc_info:
        await api.import_private_chat_workflow(
            api.ImportChatWorkflowRequest(
                slug="empty", display_name="Empty", yaml=empty,
            ),
            request(db),
            ALICE,
        )
    assert exc_info.value.status_code == 422
    detail = cast(dict[str, Any], exc_info.value.detail)
    assert "meaningful user-visible" in detail["message"]
    assert db["chat_workflows"].docs == []


@pytest.mark.asyncio
async def test_failed_generation_persists_nothing(monkeypatch):
    db = InMemoryDB()
    generate = AsyncMock(return_value={
        "success": False,
        "yaml": VALID_YAML,
        "preflight_report": {"valid": False},
    })
    monkeypatch.setattr(api, "generate_workflow_endpoint", generate)

    with pytest.raises(HTTPException) as exc_info:
        await api.generate_private_chat_workflow(
            api.GenerateChatWorkflowRequest(
                prompt="Research a company", slug="research", display_name="Research",
            ),
            request(db),
            ALICE,
        )
    assert exc_info.value.status_code == 422
    assert db["chat_workflows"].docs == []


@pytest.mark.asyncio
async def test_successful_generation_is_saved_only_as_private_draft(monkeypatch):
    db = InMemoryDB()
    generate = AsyncMock(return_value={"success": True, "yaml": VALID_YAML})
    monkeypatch.setattr(api, "generate_workflow_endpoint", generate)

    result = await api.generate_private_chat_workflow(
        api.GenerateChatWorkflowRequest(
            prompt="Research a company",
            slug="research",
            display_name="Research",
            preferred_output_type="pdf",
        ),
        request(db),
        ALICE,
    )

    assert result["source"] == "generated"
    assert result["visibility"] == "private"
    stored = db["chat_workflows"].docs[0]
    assert stored["owner_scope_id"] == "alice-scope"
    assert "visibility_status: draft" in stored["yaml"]
    assert generate.await_args is not None
    generated_request = generate.await_args.args[0]
    assert "primary user-visible output must be pdf" in generated_request.prompt