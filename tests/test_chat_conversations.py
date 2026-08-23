from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError

import app.api.chat_conversations as api
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from app.workflow.chat_conversation_store import (
    ChatConversationNotFoundError,
    ChatConversationStore,
)
from tests.fake_mongo import InMemoryDB


ALICE = CurrentUser("alice", Role.CONSULTANT, session_id="alice-scope")
BOB = CurrentUser("bob", Role.CONSULTANT, session_id="bob-scope")


def request(db: InMemoryDB | None) -> Request:
    services = {} if db is None else {"audit_db": db}
    return cast(Request, SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(services=services)),
    ))


@pytest.mark.asyncio
async def test_conversation_is_stable_per_owner_and_workflow():
    db = InMemoryDB()
    store = ChatConversationStore(db)

    first = await store.get_or_create(
        "alice-scope", workflow_source="private", workflow_id="workflow-1",
    )
    second = await store.get_or_create(
        "alice-scope", workflow_source="private", workflow_id="workflow-1",
    )
    bob = await store.get_or_create(
        "bob-scope", workflow_source="private", workflow_id="workflow-1",
    )

    assert second.conversation_id == first.conversation_id
    assert bob.conversation_id != first.conversation_id


@pytest.mark.asyncio
async def test_append_is_idempotent_ordered_and_owner_scoped():
    db = InMemoryDB()
    store = ChatConversationStore(db)
    conversation = await store.get_or_create(
        "alice-scope", workflow_source="shared", workflow_id="workflow-1",
    )

    first = await store.append_message(
        "alice-scope", conversation.conversation_id,
        message_id="message-1", role="user", content={"text": "hello"}, run_id="run-1",
    )
    duplicate = await store.append_message(
        "alice-scope", conversation.conversation_id,
        message_id="message-1", role="user", content={"text": "changed"}, run_id="run-2",
    )
    await store.append_message(
        "alice-scope", conversation.conversation_id,
        message_id="message-2", role="assistant", content={"segments": []}, run_id="run-1",
    )

    assert duplicate == first
    assert [item.message_id for item in await store.list_messages(
        "alice-scope", conversation.conversation_id,
    )] == ["message-1", "message-2"]
    with pytest.raises(ChatConversationNotFoundError):
        await store.list_messages("bob-scope", conversation.conversation_id)


@pytest.mark.asyncio
async def test_api_resolves_appends_and_replaces_intervention():
    db = InMemoryDB()
    resolved = await api.resolve_conversation(
        api.ResolveConversationBody(workflow_source="private", workflow_id="workflow-1"),
        request(db), ALICE,
    )
    conversation_id = resolved["conversation"]["id"]
    created = await api.append_message(
        conversation_id,
        api.MessageBody(
            message_id="gate-1", role="intervention",
            content={"request": {"runId": "run-1", "nodeId": "review"}, "status": "pending"},
            run_id="run-1",
        ),
        request(db), ALICE,
    )
    updated = await api.replace_message(
        conversation_id, "gate-1",
        api.ReplaceMessageBody(
            role="intervention",
            content={"request": {"runId": "run-1", "nodeId": "review"}, "status": "resolved"},
            run_id="run-1",
        ),
        request(db), ALICE,
    )

    assert created["content"]["status"] == "pending"
    assert updated["content"]["status"] == "resolved"
    with pytest.raises(HTTPException) as exc_info:
        await api.get_conversation(conversation_id, request(db), BOB)
    assert exc_info.value.status_code == 404


def test_message_payload_limit_is_enforced():
    with pytest.raises(ValidationError, match="1 MB limit"):
        api.MessageBody(
            message_id="large", role="user", content={"text": "x" * 1_000_001},
        )


@pytest.mark.asyncio
async def test_missing_transcript_store_returns_service_unavailable():
    with pytest.raises(HTTPException) as exc_info:
        await api.resolve_conversation(
            api.ResolveConversationBody(workflow_source="shared", workflow_id="workflow-1"),
            request(None), ALICE,
        )
    assert exc_info.value.status_code == 503