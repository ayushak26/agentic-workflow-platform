from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from app.api.prompt_templates import (
    FavoriteBody, TemplateBody, create_prompt_template, delete_prompt_template,
    duplicate_prompt_template, favorite_prompt_template, list_prompt_templates,
    update_prompt_template,
)
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from app.workflow.prompt_template_store import template_variables
from tests.fake_mongo import InMemoryDB

ALICE = CurrentUser("alice", Role.CONSULTANT, session_id="alice-scope")
BOB = CurrentUser("bob", Role.CONSULTANT, session_id="bob-scope")


def request(db) -> Request:
    return cast(Request, SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(services={"audit_db": db})),
    ))


def body(title="Custom"):
    return TemplateBody(
        title=title, description="A custom prompt", category="Writing",
        content="Write about {{topic}} in a {{tone}} tone. Revisit {{topic}}.",
    )


def test_variables_are_unique_and_keep_first_appearance_order():
    assert template_variables("{{topic}} {{ tone }} {{topic}} {{length}}") == [
        "topic", "tone", "length",
    ]


@pytest.mark.asyncio
async def test_list_includes_all_categories_and_only_owners_custom_templates():
    db = InMemoryDB()
    alice = await create_prompt_template(body(), request(db), ALICE)
    await create_prompt_template(body("Bob custom"), request(db), BOB)

    result = await list_prompt_templates(request(db), ALICE)
    categories = {item["category"] for item in result["templates"] if item["built_in"]}
    assert categories == {
        "Research", "Summarize", "Compare", "Extract", "Brainstorm", "Writing", "Analysis",
    }
    custom = [item for item in result["templates"] if not item["built_in"]]
    assert [item["id"] for item in custom] == [alice["id"]]
    assert custom[0]["variables"] == ["topic", "tone"]


@pytest.mark.asyncio
async def test_custom_template_update_favorite_duplicate_and_delete():
    db = InMemoryDB()
    created = await create_prompt_template(body(), request(db), ALICE)
    updated = await update_prompt_template(
        created["id"],
        TemplateBody(
            title="Updated", description="", category="Analysis",
            content="Analyze {{subject}} for {{audience}}.",
        ),
        request(db), ALICE,
    )
    assert updated["title"] == "Updated"
    assert updated["variables"] == ["subject", "audience"]

    favorite = await favorite_prompt_template(
        created["id"], FavoriteBody(favorite=True), request(db), ALICE,
    )
    assert favorite["favorite"] is True

    duplicate = await duplicate_prompt_template(created["id"], request(db), ALICE)
    assert duplicate["id"] != created["id"]
    assert duplicate["title"] == "Updated copy"

    deleted = await delete_prompt_template(created["id"], request(db), ALICE)
    assert deleted["deleted"] is True


@pytest.mark.asyncio
async def test_owner_cannot_update_or_delete_another_users_template():
    db = InMemoryDB()
    created = await create_prompt_template(body(), request(db), ALICE)
    with pytest.raises(HTTPException) as update_error:
        await update_prompt_template(created["id"], body("Stolen"), request(db), BOB)
    assert update_error.value.status_code == 404
    with pytest.raises(HTTPException) as delete_error:
        await delete_prompt_template(created["id"], request(db), BOB)
    assert delete_error.value.status_code == 404


@pytest.mark.asyncio
async def test_builtin_is_immutable_but_can_be_duplicated():
    db = InMemoryDB()
    with pytest.raises(HTTPException) as exc_info:
        await update_prompt_template(
            "builtin_research_brief", body(), request(db), ALICE,
        )
    assert exc_info.value.status_code == 409
    copied = await duplicate_prompt_template(
        "builtin_research_brief", request(db), ALICE,
    )
    assert copied["built_in"] is False
    assert copied["category"] == "Research"