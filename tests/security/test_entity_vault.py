import asyncio

import pytest

from app.config import settings
from app.security.entity_protection_errors import VaultKeyMisconfiguredError
from app.security.entity_vault import EntityVault
from tests.security._fake_mongo import FakeAsyncDatabase

TEST_KEY = "a" * 40  # >=32 bytes, not a placeholder, distinct from secret_key


@pytest.fixture()
def vault_key():
    original = settings.entity_vault_master_key
    settings.entity_vault_master_key = TEST_KEY
    yield
    settings.entity_vault_master_key = original


@pytest.fixture()
def vault(vault_key) -> EntityVault:
    return EntityVault(FakeAsyncDatabase())


async def test_round_trip_placeholder_and_resolution(vault: EntityVault):
    placeholder = await vault.get_or_create_placeholder(
        session_id="s1",
        collection_id="c1",
        entity_type="organisation",
        real_value="Hamburg University of Applied Sciences",
        source="manual",
    )
    assert placeholder.startswith("[[ENTITY_ORGANISATION_")

    resolved = await vault.resolve_placeholders(
        session_id="s1", collection_id="c1", placeholders={placeholder}
    )
    assert resolved == {placeholder: "Hamburg University of Applied Sciences"}


async def test_same_entity_returns_stable_placeholder(vault: EntityVault):
    first = await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="organisation",
        real_value="Acme Robotics GmbH", source="manual",
    )
    second = await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="organisation",
        real_value="Acme Robotics GmbH", source="manual",
    )
    assert first == second


async def test_case_and_whitespace_normalized_to_same_entity(vault: EntityVault):
    first = await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="organisation",
        real_value="Acme  Robotics GmbH", source="manual",
    )
    second = await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="organisation",
        real_value="acme robotics gmbh", source="manual",
    )
    assert first == second


async def test_cross_scope_isolation_gets_independent_placeholders(vault: EntityVault):
    p1 = await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="organisation",
        real_value="Acme Robotics GmbH", source="manual",
    )
    p2 = await vault.get_or_create_placeholder(
        session_id="s2", collection_id="c1", entity_type="organisation",
        real_value="Acme Robotics GmbH", source="manual",
    )
    assert p1 == "[[ENTITY_ORGANISATION_1]]"
    assert p2 == "[[ENTITY_ORGANISATION_1]]"
    # Distinct scopes never share a mapping row:
    r1 = await vault.resolve_placeholders(
        session_id="s1", collection_id="c1", placeholders={p1}
    )
    r2 = await vault.resolve_placeholders(
        session_id="s2", collection_id="c1", placeholders={p2}
    )
    assert r1 == {p1: "Acme Robotics GmbH"}
    assert r2 == {p2: "Acme Robotics GmbH"}
    # A scope can't resolve another scope's placeholder.
    cross = await vault.resolve_placeholders(
        session_id="s1", collection_id="c9", placeholders={p1}
    )
    assert cross == {}


async def test_different_entities_never_collide_on_placeholder(vault: EntityVault):
    p1 = await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="organisation",
        real_value="Acme Robotics GmbH", source="manual",
    )
    p2 = await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="organisation",
        real_value="Beta Systems SA", source="manual",
    )
    assert p1 != p2


async def test_concurrent_creation_of_same_entity_converges_on_one_placeholder(
    vault: EntityVault,
):
    results = await asyncio.gather(
        *[
            vault.get_or_create_placeholder(
                session_id="s1", collection_id="c1", entity_type="organisation",
                real_value="Acme Robotics GmbH", source="auto_detected",
            )
            for _ in range(10)
        ]
    )
    assert len(set(results)) == 1


async def test_concurrent_creation_of_different_entities_never_collide(
    vault: EntityVault,
):
    results = await asyncio.gather(
        *[
            vault.get_or_create_placeholder(
                session_id="s1", collection_id="c1", entity_type="organisation",
                real_value=f"Org {i}", source="auto_detected",
            )
            for i in range(10)
        ]
    )
    assert len(set(results)) == 10


async def test_delete_entity_removes_mapping(vault: EntityVault):
    await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="client",
        real_value="Initech", source="manual",
    )
    deleted = await vault.delete_entity(
        session_id="s1", collection_id="c1", entity_type="client", value="Initech"
    )
    assert deleted is True
    entities = await vault.list_scope_entities(session_id="s1", collection_id="c1")
    assert entities == []


async def test_wrong_key_fails_to_decrypt(vault: EntityVault):
    placeholder = await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="person",
        real_value="Jane Smith", source="manual",
    )
    settings.entity_vault_master_key = "b" * 40  # different key
    with pytest.raises(VaultKeyMisconfiguredError):
        await vault.resolve_placeholders(
            session_id="s1", collection_id="c1", placeholders={placeholder}
        )


async def test_missing_key_raises_before_any_mongo_write(vault_key):
    settings.entity_vault_master_key = ""
    vault = EntityVault(FakeAsyncDatabase())
    with pytest.raises(VaultKeyMisconfiguredError):
        await vault.get_or_create_placeholder(
            session_id="s1", collection_id="c1", entity_type="person",
            real_value="Jane Smith", source="manual",
        )


async def test_key_reusing_secret_key_is_rejected(vault_key):
    settings.entity_vault_master_key = settings.secret_key
    vault = EntityVault(FakeAsyncDatabase())
    with pytest.raises(VaultKeyMisconfiguredError):
        await vault.get_or_create_placeholder(
            session_id="s1", collection_id="c1", entity_type="person",
            real_value="Jane Smith", source="manual",
        )


async def test_aad_tamper_is_rejected(vault: EntityVault):
    """Re-attaching one entity's ciphertext to a different placeholder/scope
    must fail the GCM tag check, not silently decrypt to garbage."""
    placeholder = await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="organisation",
        real_value="Acme Robotics GmbH", source="manual",
    )
    raw_doc = await vault._mappings.find_one(
        {"session_id": "s1", "collection_id": "c1", "placeholder": placeholder}
    )
    tampered_id = "s1::c9::tampered"
    tampered = dict(raw_doc)
    tampered["_id"] = tampered_id
    tampered["collection_id"] = "c9"  # AAD now mismatches the stored ciphertext
    await vault._mappings.insert_one(tampered)

    with pytest.raises(Exception):
        await vault.resolve_placeholders(
            session_id="s1", collection_id="c9", placeholders={placeholder}
        )


async def test_expiry_fields_set_relative_to_ttl_setting(vault: EntityVault):
    from datetime import timedelta

    placeholder = await vault.get_or_create_placeholder(
        session_id="s1", collection_id="c1", entity_type="domain",
        real_value="example.com", source="auto_detected",
    )
    doc = await vault._mappings.find_one(
        {"session_id": "s1", "collection_id": "c1", "placeholder": placeholder}
    )
    delta = doc["expires_at"] - doc["created_at"]
    assert abs(delta - timedelta(seconds=settings.entity_mapping_ttl_seconds)) < timedelta(
        seconds=5
    )
