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
async def test_private_workflow_list_hides_managed_and_legacy_workspace_adapters_by_default():
    db = InMemoryDB()
    store = ChatWorkflowStore(db)
    authored = await store.create(
        owner_scope_id="alice-scope", slug="my-workflow", display_name="My Workflow",
        description="", yaml_text=VALID_YAML, source="imported",
    )
    managed = await store.create(
        owner_scope_id="alice-scope", slug="managed-web", display_name="Web Assistant",
        description="", yaml_text=VALID_YAML, source="imported",
        managed=True, adapter_key="web:default", adapter_fingerprint="abc",
    )
    legacy = await store.create(
        owner_scope_id="alice-scope", slug="workspace-legacy", display_name="Legacy Adapter",
        description="", yaml_text=VALID_YAML, source="imported",
    )

    default_result = await api.list_private_chat_workflows(request(db), ALICE)
    all_result = await api.list_private_chat_workflows(request(db), ALICE, include_managed=True)

    assert [item["id"] for item in default_result["workflows"]] == [authored.chat_workflow_id]
    assert {item["id"] for item in all_result["workflows"]} == {
        authored.chat_workflow_id, managed.chat_workflow_id, legacy.chat_workflow_id,
    }


@pytest.mark.asyncio
async def test_managed_adapter_upgrades_canonical_yaml_in_place():
    db = InMemoryDB()
    first = await api.ensure_managed_adapter(
        request(db), ALICE,
        adapter_key="capability:test", display_name="Managed Test",
        yaml_text=VALID_YAML, source="imported",
    )
    updated_yaml = VALID_YAML.replace("value: hello", "value: upgraded")
    second = await api.ensure_managed_adapter(
        request(db), ALICE,
        adapter_key="capability:test", display_name="Managed Test v2",
        yaml_text=updated_yaml, source="imported",
    )

    assert second.chat_workflow_id == first.chat_workflow_id
    assert second.display_name == "Managed Test v2"
    assert "value: upgraded" in second.yaml
    assert second.adapter_fingerprint != first.adapter_fingerprint
    assert len(db["chat_workflows"].docs) == 1


@pytest.mark.asyncio
async def test_builder_execution_adapter_adds_universal_input_and_output_llm_calls():
    result = await api.get_builder_chat_execution_adapter("verder_email_intake", ALICE)

    assert result["adapted"] is True
    assert result["yaml"].count("type: TransformAgent") >= 2
    assert "workflow: verder_email_intake" in result["yaml"]
    assert "email_text: '{{outputs.prepare_inputs.parsed.email_text}}'" in result["yaml"]
    assert "source_file: '{{outputs.prepare_inputs.parsed.source_file}}'" in result["yaml"]
    assert "processed_at: '{{outputs.prepare_inputs.parsed.processed_at}}'" in result["yaml"]
    assert "structured_result: '{{outputs.run_workflow.result}}'" in result["yaml"]


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
    assert "WebSearchAgent" in detail["yaml"]
    assert "paper-search-mcp" in detail["yaml"]
    assert "WorkflowFileLoader" in detail["yaml"]
    assert "gpt-5.6-sol" in detail["yaml"]
    assert "visibility_status: draft" in detail["yaml"]


@pytest.mark.asyncio
async def test_deep_research_rag_variant_is_reusable_and_bound_to_saved_agent():
    db = InMemoryDB()
    first = await api.ensure_deep_research_chat_workflow(request(db), ALICE, "rag-agent-1")
    second = await api.ensure_deep_research_chat_workflow(request(db), ALICE, "rag-agent-1")
    other = await api.ensure_deep_research_chat_workflow(request(db), ALICE, "rag-agent-2")

    assert first["id"] == second["id"]
    assert other["id"] != first["id"]
    detail = await api.get_private_chat_workflow(first["id"], request(db), ALICE)
    assert "type: RAGAgent" in detail["yaml"]
    assert "rag_agent_id: rag-agent-1" in detail["yaml"]
    assert "paper-search-mcp" in detail["yaml"]


@pytest.mark.asyncio
async def test_general_chat_preset_is_idempotent_owner_scoped_and_saved():
    db = InMemoryDB()
    alice_first = await api.ensure_general_chat_workflow(request(db), ALICE)
    alice_second = await api.ensure_general_chat_workflow(request(db), ALICE)
    bob = await api.ensure_general_chat_workflow(request(db), BOB)

    assert alice_first["id"] == alice_second["id"]
    assert bob["id"] != alice_first["id"]
    assert alice_first["slug"] == "general-chat"
    assert alice_first["name"] == "General Chat"
    detail = await api.get_private_chat_workflow(alice_first["id"], request(db), ALICE)
    assert "name: General Chat" in detail["yaml"]
    assert "TransformAgent" in detail["yaml"]
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
async def test_get_repairs_legacy_planner_wrapper_for_chatbot_workflow():
    db = InMemoryDB()
    source_name = "w01_intelligent_customer_inquiry_resolution"
    legacy_yaml = """
name: Legacy Wrapper
version: '1.0'
entry: start
exit: reply
nodes:
  - id: start
    type: StartAgent
    config:
      mode: chatbot
  - id: run_workflow
    type: SubprocessAgent
    config:
      workflow: w01_intelligent_customer_inquiry_resolution
      inputs:
        attachments: '{{outputs.start.attachments}}'
      result_from: workflow_output
  - id: reply
    type: EndAgent
    config:
      mode: chat_response
      chat_message: done
edges:
  - from: start
    to: run_workflow
  - from: run_workflow
    to: reply
"""
    record = await ChatWorkflowStore(db).create(
        owner_scope_id="alice-scope",
        slug="workspace-legacy",
        display_name="Legacy",
        description="",
        yaml_text=legacy_yaml,
        source="existing",
        source_workflow_name=source_name,
    )

    detail = await api.get_private_chat_workflow(record.chat_workflow_id, request(db), ALICE)

    assert "message: '{{outputs.prepare_inputs.parsed.message}}'" in detail["yaml"]
    assert detail["yaml"].count("type: TransformAgent") >= 2
    assert "structured_result: '{{outputs.run_workflow.result}}'" in detail["yaml"]
    stored = db["chat_workflows"].docs[0]
    assert stored["yaml"] == detail["yaml"]
    assert stored["updated_at"] != record.updated_at


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