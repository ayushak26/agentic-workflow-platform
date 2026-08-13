"""Knowledge Studio resource lifecycle, versioning and security-boundary tests."""
from __future__ import annotations

import pytest

from app.knowledge.ids import kind_of, new_resource_id, scoped_legacy_id
from app.knowledge.models import (
    ChunkingProfileConfig,
    CollectionResource,
    EmbeddingProfileConfig,
    GenerationProfileConfig,
    ParserProfileConfig,
    ProfileType,
    ResourceStatus,
    RetrievalProfileConfig,
)
from app.knowledge.repository import KnowledgeRepository, ResourceNotFoundError
from app.knowledge.service import KnowledgeService, validate_metadata_schema, workspace_for_scope
from app.retrieval.filters import (
    MetadataFilterValidationError,
    RESERVED_METADATA_FIELDS,
    coerce_metadata_filter_group,
    validate_metadata_document,
    validate_metadata_filters,
)
from app.retrieval.models import MetadataFilterGroup, MetadataFilterPredicate
from tests.fake_mongo import InMemoryDB


def _repository() -> KnowledgeRepository:
    return KnowledgeRepository(InMemoryDB())


# ---- IDs ---------------------------------------------------------------

def test_new_resource_id_has_expected_prefix_and_is_unique():
    first = new_resource_id("collection")
    second = new_resource_id("collection")
    assert first.startswith("col_")
    assert first != second
    assert kind_of(first) == "collection"


def test_scoped_legacy_id_is_deterministic_and_scope_sensitive():
    a = scoped_legacy_id("collection", "owner-1", "legacy-key")
    b = scoped_legacy_id("collection", "owner-1", "legacy-key")
    c = scoped_legacy_id("collection", "owner-2", "legacy-key")
    assert a == b
    assert a != c
    assert kind_of(a) == "collection"


def test_unknown_resource_id_kind_is_none_not_an_error():
    assert kind_of("not-a-real-id") is None
    assert kind_of("legacy_collection_123") is None


# ---- Collection + profile lifecycle -------------------------------------

@pytest.mark.asyncio
async def test_create_collection_and_round_trip_through_repository():
    service = KnowledgeService(_repository())
    created = await service.create_collection(owner_scope_id="owner-a", name="Dura 25 Knowledge")
    fetched = await service.repository.get_collection("owner-a", created.collection_id)
    assert fetched.name == "Dura 25 Knowledge"
    assert fetched.status == ResourceStatus.DRAFT
    assert fetched.workspace_id == workspace_for_scope("owner-a")


@pytest.mark.asyncio
async def test_collection_lookup_is_owner_scoped():
    service = KnowledgeService(_repository())
    created = await service.create_collection(owner_scope_id="owner-a", name="Owner A's Collection")
    with pytest.raises(ResourceNotFoundError):
        await service.repository.get_collection("owner-b", created.collection_id)


@pytest.mark.asyncio
async def test_profile_updates_create_new_versions_not_rewrites():
    service = KnowledgeService(_repository())
    v1 = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.CHUNKING,
        name="My Chunking", strategy="recursive", config={},
    )
    v2 = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.CHUNKING,
        name="My Chunking", strategy="recursive", config={"target_tokens": 256},
        profile_id=v1.profile_id,
    )
    assert v2.version == v1.version + 1
    assert v2.profile_id == v1.profile_id
    latest = await service.repository.get_profile("owner-a", v1.profile_id, expected_type=ProfileType.CHUNKING)
    assert latest.version == v2.version
    original = await service.repository.get_profile("owner-a", v1.profile_id, version=1)
    assert original.config.get("target_tokens") != 256


@pytest.mark.asyncio
async def test_get_profile_rejects_wrong_expected_type():
    service = KnowledgeService(_repository())
    profile = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.PARSER,
        name="Standard", strategy="standard", config={},
    )
    with pytest.raises(ResourceNotFoundError):
        await service.repository.get_profile(
            "owner-a", profile.profile_id, expected_type=ProfileType.CHUNKING
        )


@pytest.mark.asyncio
async def test_ensure_default_profiles_is_idempotent():
    service = KnowledgeService(_repository())
    first = await service.ensure_default_profiles("owner-a")
    second = await service.ensure_default_profiles("owner-a")
    assert first["parser"].profile_id == second["parser"].profile_id
    assert first["parser"].version == second["parser"].version


@pytest.mark.parametrize(
    "profile_type,model",
    [
        (ProfileType.PARSER, ParserProfileConfig),
        (ProfileType.CHUNKING, ChunkingProfileConfig),
        (ProfileType.EMBEDDING, EmbeddingProfileConfig),
        (ProfileType.RETRIEVAL, RetrievalProfileConfig),
        (ProfileType.GENERATION, GenerationProfileConfig),
    ],
)
def test_validate_profile_config_applies_the_right_schema(profile_type, model):
    validated = KnowledgeService.validate_profile_config(profile_type, {})
    assert validated == model().model_dump(mode="json")


def test_chunking_profile_rejects_overlap_ge_target():
    with pytest.raises(ValueError):
        ChunkingProfileConfig(target_tokens=100, overlap_tokens=100)


def test_retrieval_profile_rejects_final_count_over_candidate_count():
    with pytest.raises(ValueError):
        RetrievalProfileConfig(candidate_count=5, final_count=10)


# ---- Index creation and activation --------------------------------------

@pytest.mark.asyncio
async def test_create_index_derives_a_stable_embedding_fingerprint():
    service = KnowledgeService(_repository())
    collection = await service.create_collection(owner_scope_id="owner-a", name="Coll")
    parser = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.PARSER, name="P", strategy="standard", config={},
    )
    chunking = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.CHUNKING, name="C", strategy="recursive", config={},
    )
    embedding = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.EMBEDDING, name="E", strategy="openai", config={},
    )
    index = await service.create_index(
        owner_scope_id="owner-a", collection_id=collection.collection_id,
        parser_profile_id=parser.profile_id, parser_profile_version=parser.version,
        chunking_profile_id=chunking.profile_id, chunking_profile_version=chunking.version,
        embedding_profile_id=embedding.profile_id, embedding_profile_version=embedding.version,
    )
    assert index.version == 1
    assert index.embedding_fingerprint
    assert index.physical_collection.startswith("DocumentChunk_")


@pytest.mark.asyncio
async def test_activate_index_deactivates_the_previous_active_index():
    service = KnowledgeService(_repository())
    collection = await service.create_collection(owner_scope_id="owner-a", name="Coll")
    parser = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.PARSER, name="P", strategy="standard", config={},
    )
    chunking = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.CHUNKING, name="C", strategy="recursive", config={},
    )
    embedding = await service.create_profile_version(
        owner_scope_id="owner-a", profile_type=ProfileType.EMBEDDING, name="E", strategy="openai", config={},
    )
    kwargs = dict(
        owner_scope_id="owner-a", collection_id=collection.collection_id,
        parser_profile_id=parser.profile_id, parser_profile_version=parser.version,
        chunking_profile_id=chunking.profile_id, chunking_profile_version=chunking.version,
        embedding_profile_id=embedding.profile_id, embedding_profile_version=embedding.version,
    )
    first_index = await service.create_index(**kwargs)
    first_index.status = ResourceStatus.READY
    await service.repository.save_index(first_index)
    await service.activate_index(
        owner_scope_id="owner-a", collection_id=collection.collection_id, index_id=first_index.index_id
    )

    second_index = await service.create_index(**kwargs)
    second_index.status = ResourceStatus.READY
    await service.repository.save_index(second_index)
    await service.activate_index(
        owner_scope_id="owner-a", collection_id=collection.collection_id, index_id=second_index.index_id
    )

    refreshed_first = await service.repository.get_index("owner-a", first_index.index_id)
    refreshed_second = await service.repository.get_index("owner-a", second_index.index_id)
    updated_collection = await service.repository.get_collection("owner-a", collection.collection_id)
    assert refreshed_first.status == ResourceStatus.READY
    assert refreshed_second.status == ResourceStatus.ACTIVE
    assert updated_collection.active_index_id == second_index.index_id


# ---- Metadata schema validation (security boundary) ---------------------

def test_metadata_schema_cannot_redefine_a_reserved_field():
    with pytest.raises(ValueError):
        validate_metadata_schema({"properties": {"collection_id": {"type": "string"}}})


def test_metadata_schema_rejects_unsupported_type():
    with pytest.raises(ValueError):
        validate_metadata_schema({"properties": {"weight": {"type": "binary"}}})


def test_validate_metadata_document_accepts_standard_fields_without_schema_declaration():
    validate_metadata_document({"industry": "chemicals", "doc_type": "manual"}, schema={})


def test_validate_metadata_document_rejects_undeclared_custom_field():
    with pytest.raises(MetadataFilterValidationError):
        validate_metadata_document({"secret_internal_flag": True}, schema={})


@pytest.mark.parametrize("field_name", sorted(RESERVED_METADATA_FIELDS))
def test_validate_metadata_filters_rejects_every_reserved_field(field_name):
    group = MetadataFilterGroup(predicates=[MetadataFilterPredicate(field=field_name, value="x")])
    with pytest.raises(MetadataFilterValidationError):
        validate_metadata_filters(group, schema={})


def test_validate_metadata_filters_accepts_a_schema_declared_field():
    schema = {"properties": {"product": {"type": "string"}}}
    group = MetadataFilterGroup(predicates=[MetadataFilterPredicate(field="product", value="Dura 25")])
    validate_metadata_filters(group, schema)  # must not raise


def test_coerce_metadata_filter_group_turns_flat_runtime_filters_into_equals_predicates():
    group = coerce_metadata_filter_group({"product": "Dura 25"})
    assert group is not None
    assert group.predicates[0].field == "product"
    assert group.predicates[0].operator == "equals"


def test_coerce_metadata_filter_group_passes_through_an_already_typed_group():
    original = MetadataFilterGroup(predicates=[MetadataFilterPredicate(field="product", value="x")])
    assert coerce_metadata_filter_group(original) is original


@pytest.mark.parametrize("empty", [None, {}])
def test_coerce_metadata_filter_group_returns_none_for_empty_input(empty):
    assert coerce_metadata_filter_group(empty) is None
