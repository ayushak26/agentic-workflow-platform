import pytest

from app.config import settings
from app.security.entity_ner import EntityMatch
from app.security.entity_tokenizer import EntityTokenizerService, ProcessingMode
from tests.security._fake_mongo import FakeAsyncDatabase

TEST_KEY = "c" * 40


@pytest.fixture()
def vault_key():
    original = settings.entity_vault_master_key
    settings.entity_vault_master_key = TEST_KEY
    yield
    settings.entity_vault_master_key = original


@pytest.fixture()
def service(vault_key) -> EntityTokenizerService:
    return EntityTokenizerService(FakeAsyncDatabase())


async def test_public_mode_is_a_pure_passthrough(service: EntityTokenizerService):
    text = "Acme Robotics GmbH will lead the project."
    result = await service.tokenize(
        text, session_id="s1", collection_id="c1", mode=ProcessingMode.PUBLIC
    )
    assert result.text == text
    assert result.placeholders_used == frozenset()


async def test_registered_organisation_is_tokenized(service: EntityTokenizerService):
    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="Hamburg University of Applied Sciences",
    )
    result = await service.tokenize(
        "Hamburg University of Applied Sciences will conduct laboratory validation.",
        session_id="s1", collection_id="c1",
    )
    assert "Hamburg University of Applied Sciences" not in result.text
    assert "will conduct laboratory validation" in result.text
    assert len(result.placeholders_used) == 1


async def test_stable_placeholder_across_calls_in_same_scope(
    service: EntityTokenizerService,
):
    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="Acme Robotics GmbH",
    )
    r1 = await service.tokenize(
        "Acme Robotics GmbH leads work package 1.", session_id="s1", collection_id="c1"
    )
    r2 = await service.tokenize(
        "As noted, Acme Robotics GmbH also leads WP2.",
        session_id="s1", collection_id="c1",
    )
    ph1 = next(iter(r1.placeholders_used))
    ph2 = next(iter(r2.placeholders_used))
    assert ph1 == ph2


async def test_cross_scope_isolation(service: EntityTokenizerService):
    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="Acme Robotics GmbH",
    )
    result = await service.tokenize(
        "Acme Robotics GmbH is unrelated here.", session_id="s2", collection_id="c1"
    )
    # Not registered in s2's scope -> falls to the NER safety net, which will
    # still tokenize it as an auto-detected ORG, but under a DIFFERENT vault
    # entry than s1's -- confirm the two scopes' placeholders differ.
    ph_s2 = next(iter(result.placeholders_used))
    r1 = await service.tokenize(
        "Acme Robotics GmbH leads work package 1.", session_id="s1", collection_id="c1"
    )
    ph_s1 = next(iter(r1.placeholders_used))
    resolved_s1 = await service._vault.resolve_placeholders(
        session_id="s1", collection_id="c1", placeholders={ph_s1}
    )
    resolved_s2 = await service._vault.resolve_placeholders(
        session_id="s2", collection_id="c1", placeholders={ph_s2}
    )
    assert resolved_s1 == {ph_s1: "Acme Robotics GmbH"}
    assert resolved_s2 == {ph_s2: "Acme Robotics GmbH"}
    # Per-scope placeholder numbering is independent, so ph_s1/ph_s2 may be
    # the identical string -- that's expected (each scope has its own vault
    # row) and not itself a leak. Real isolation: a scope that never saw the
    # entity can't resolve it, even if the placeholder string matches.
    assert await service._vault.resolve_placeholders(
        session_id="s1", collection_id="untouched-scope", placeholders={ph_s1, ph_s2}
    ) == {}


async def test_email_is_detected_and_tokenized(service: EntityTokenizerService):
    result = await service.tokenize(
        "Contact jane.smith@example.com for details.",
        session_id="s1", collection_id="c1",
    )
    assert "jane.smith@example.com" not in result.text
    assert "[[ENTITY_EMAIL_1]]" in result.text


async def test_phone_is_detected_and_tokenized(service: EntityTokenizerService):
    result = await service.tokenize(
        "Call +49 40 123456789 for the coordinator.",
        session_id="s1", collection_id="c1",
    )
    assert "+49 40 123456789" not in result.text


async def test_grant_agreement_number_is_detected(service: EntityTokenizerService):
    result = await service.tokenize(
        "This work is funded under Grant Agreement No. 101070123.",
        session_id="s1", collection_id="c1",
    )
    assert "101070123" not in result.text
    assert "Grant Agreement No." in result.text  # surrounding prose preserved


async def test_domain_is_detected(service: EntityTokenizerService):
    result = await service.tokenize(
        "See https://acme-robotics.example.com for more.",
        session_id="s1", collection_id="c1",
    )
    assert "acme-robotics.example.com" not in result.text


async def test_ner_safety_net_catches_unregistered_person_and_org(
    service: EntityTokenizerService,
):
    result = await service.tokenize(
        "Dr. Jane Smith at Beta Systems SA will present the results.",
        session_id="s1", collection_id="c1",
    )
    assert "Jane Smith" not in result.text
    assert "Beta Systems SA" not in result.text
    assert len(result.placeholders_used) == 2


async def test_ner_safety_net_does_not_tokenize_ambiguous_short_acronyms(
    service: EntityTokenizerService, monkeypatch,
):
    monkeypatch.setattr(
        "app.security.entity_tokenizer.extract_entities",
        lambda text: [EntityMatch(start=16, end=18, text="AI", entity_type="organisation")],
    )
    result = await service.tokenize(
        "Identify top 10 AI companies.", session_id="s1", collection_id="c1",
    )
    assert result.text == "Identify top 10 AI companies."
    assert result.placeholders_used == frozenset()


async def test_registered_short_acronym_remains_protected(
    service: EntityTokenizerService, monkeypatch,
):
    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation", value="AI",
    )
    monkeypatch.setattr(
        "app.security.entity_tokenizer.extract_entities",
        lambda text: [EntityMatch(start=16, end=18, text="AI", entity_type="organisation")],
    )
    result = await service.tokenize(
        "Identify top 10 AI companies.", session_id="s1", collection_id="c1",
    )
    assert "AI" not in result.text
    assert "[[ENTITY_ORGANISATION_1]]" in result.text


async def test_stale_auto_detected_short_acronym_is_ignored_but_manual_is_not(
    service: EntityTokenizerService, monkeypatch,
):
    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="AI", source="auto_detected",
    )
    monkeypatch.setattr("app.security.entity_tokenizer.extract_entities", lambda text: [])
    result = await service.tokenize(
        "Create a list of 100 AI companies in Europe.",
        session_id="s1", collection_id="c1",
    )
    assert result.text == "Create a list of 100 AI companies in Europe."
    assert result.placeholders_used == frozenset()

    await service.registry.register(
        session_id="s2", collection_id="c1", entity_type="project_acronym",
        value="AI", source="manual",
    )
    protected = await service.tokenize(
        "Create a list of 100 AI companies in Europe.",
        session_id="s2", collection_id="c1",
    )
    assert "AI" not in protected.text
    assert protected.placeholders_used


async def test_registry_hit_wins_over_ner_on_overlap(service: EntityTokenizerService):
    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="partner",
        value="Beta Systems SA",
    )
    result = await service.tokenize(
        "Beta Systems SA will lead this task.", session_id="s1", collection_id="c1"
    )
    # Only one placeholder for the whole "Beta Systems SA" span -- NER
    # matching a sub/overlapping span didn't create a second, competing entry.
    assert len(result.placeholders_used) == 1
    entities = await service.registry.list_entities(
        session_id="s1", collection_id="c1"
    )
    assert any(e["entity_type"] == "partner" for e in entities)
    assert not any(e["entity_type"] == "organisation" for e in entities)


async def test_json_blob_text_is_scanned_too(service: EntityTokenizerService):
    import json

    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="Acme Robotics GmbH",
    )
    blob = json.dumps({"lead_partner": "Acme Robotics GmbH", "wp": 1})
    result = await service.tokenize(blob, session_id="s1", collection_id="c1")
    assert "Acme Robotics GmbH" not in result.text
    assert '"wp": 1' in result.text


async def test_round_trip_tokenize_then_detokenize(service: EntityTokenizerService):
    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="Acme Robotics GmbH",
    )
    original = "Acme Robotics GmbH will lead work package 3."
    tokenized = await service.tokenize(original, session_id="s1", collection_id="c1")
    detok = await service.detokenize(
        tokenized.text, session_id="s1", collection_id="c1"
    )
    assert detok.value == original
    assert detok.unresolved_placeholders == frozenset()


async def test_detokenize_walks_nested_structures(service: EntityTokenizerService):
    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="client",
        value="Initech",
    )
    tokenized = await service.tokenize(
        "Initech is the client.", session_id="s1", collection_id="c1"
    )
    placeholder = next(iter(tokenized.placeholders_used))
    nested = {"summary": tokenized.text, "tags": [placeholder, "unrelated"]}
    detok = await service.detokenize(nested, session_id="s1", collection_id="c1")
    assert detok.value["summary"] == "Initech is the client."
    assert detok.value["tags"] == ["Initech", "unrelated"]


async def test_unresolved_placeholder_is_flagged_not_guessed(
    service: EntityTokenizerService,
):
    detok = await service.detokenize(
        "The plan involves [[ENTITY_ORGANISATION_999]].",
        session_id="s1", collection_id="c1",
    )
    assert detok.unresolved_placeholders == frozenset({"[[ENTITY_ORGANISATION_999]]"})
    # Left as-is, never guessed at:
    assert "[[ENTITY_ORGANISATION_999]]" in detok.value


async def test_response_leak_of_registered_value_is_audited_not_blocked(
    service: EntityTokenizerService,
):
    """The core guarantee is on the INPUT side (proven at the gateway
    boundary in tests/llm/). A verbatim match in the OUTPUT can't be told
    apart from the model correctly guessing/inferring a name, or a noisy
    auto-detected entity reappearing -- both far more common in practice
    than a real leak -- so this is audit-only and must never raise."""
    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="organisation",
        value="Acme Robotics GmbH",
    )
    detok = await service.detokenize(
        "The coordinator is Acme Robotics GmbH.",
        session_id="s1", collection_id="c1",
    )
    assert detok.value == "The coordinator is Acme Robotics GmbH."


async def test_restricted_local_mode_falls_back_to_pseudonymised(
    service: EntityTokenizerService,
):
    await service.registry.register(
        session_id="s1", collection_id="c1", entity_type="client", value="Initech",
    )
    result = await service.tokenize(
        "Initech is the client.", session_id="s1", collection_id="c1",
        mode=ProcessingMode.RESTRICTED_LOCAL,
    )
    assert "Initech" not in result.text
