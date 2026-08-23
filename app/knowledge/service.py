"""Authorization-aware operations over Knowledge Studio resources."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .ids import new_resource_id, scoped_legacy_id
from .models import (
    ChunkingProfileConfig,
    CollectionResource,
    EmbeddingProfileConfig,
    GenerationProfileConfig,
    IndexVersion,
    ParserProfileConfig,
    ProfileType,
    ProfileVersion,
    RAGAgentDefinition,
    ResourceStatus,
    RetrievalProfileConfig,
    RetrievalRoutingProfileConfig,
)
from .repository import KnowledgeRepository, ResourceConflictError


def validate_metadata_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Validate the metadata schema.

    Args:
        schema (dict[str, Any]): Schema definition.

    Returns:
        dict[str, Any]: The metadata schema.
    """
    properties = schema.get("properties", schema)
    if not isinstance(properties, dict):
        raise ValueError("metadata_schema properties must be an object")
    reserved = {
        "session_id", "owner_scope_id", "workspace_id", "collection_id",
        "index_id", "document_id", "source_id", "source_version_id",
    }
    allowed_types = {"string", "number", "integer", "boolean", "date", "datetime"}
    standard_types = {
        "department": {"string"},
        "document_type": {"string"},
        "country": {"string"},
        "product": {"string"},
        "version": {"string"},
        "publish_date": {"date", "datetime"},
        "customer": {"string"},
        "industry": {"string"},
        "doc_type": {"string"},
        "language": {"string"},
    }
    for field, definition in properties.items():
        if field in reserved:
            raise ValueError(f"metadata schema cannot redefine security field {field!r}")
        value_type = definition.get("type") if isinstance(definition, dict) else definition
        if value_type not in allowed_types:
            raise ValueError(
                f"metadata field {field!r} has unsupported type {value_type!r}"
            )
        if field in standard_types and value_type not in standard_types[field]:
            raise ValueError(
                f"metadata field {field!r} must use one of "
                f"{sorted(standard_types[field])} for the shared search schema"
            )
    return schema


def workspace_for_scope(owner_scope_id: str) -> str:
    """Compute the workspace for scope.

    Args:
        owner_scope_id (str): The owner scope id.

    Returns:
        str: The for scope.
    """
    return scoped_legacy_id("workspace", owner_scope_id, "default")


class KnowledgeService:
    """Provides the KnowledgeService behaviour."""
    def __init__(self, repository: KnowledgeRepository):
        """Initialize the KnowledgeService.

        Args:
            repository (KnowledgeRepository): The repository.
        """
        self.repository = repository

    async def create_collection(
        self,
        *,
        owner_scope_id: str,
        name: str,
        description: str = "",
        metadata_schema: dict[str, Any] | None = None,
        doc_types: list[str] | None = None,
    ) -> CollectionResource:
        """Create the collection.

        Args:
            owner_scope_id (str): The owner scope id.
            name (str): Workflow or resource name.
            description (str): The description (optional, default '').
            metadata_schema (dict[str, Any] | None): The metadata schema (optional, default None).
            doc_types (list[str] | None): The doc types (optional, default None).

        Returns:
            CollectionResource: The collection.
        """
        resource = CollectionResource(
            collection_id=new_resource_id("collection"),
            workspace_id=workspace_for_scope(owner_scope_id),
            owner_scope_id=owner_scope_id,
            name=name,
            description=description,
            metadata_schema=validate_metadata_schema(metadata_schema or {}),
            doc_types=doc_types or ["general"],
        )
        return await self.repository.create_collection(resource)

    async def create_profile_version(
        self,
        *,
        owner_scope_id: str,
        profile_type: ProfileType,
        name: str,
        strategy: str,
        config: dict[str, Any],
        profile_id: str | None = None,
        description: str = "",
        based_on_preset: str | None = None,
    ) -> ProfileVersion:
        """Create the profile version.

        Args:
            owner_scope_id (str): The owner scope id.
            profile_type (ProfileType): The profile type.
            name (str): Workflow or resource name.
            strategy (str): The strategy.
            config (dict[str, Any]): Node configuration mapping.
            profile_id (str | None): The profile id (optional, default None).
            description (str): The description (optional, default '').
            based_on_preset (str | None): The based on preset (optional, default None).

        Returns:
            ProfileVersion: The profile version.
        """
        id_kind = {
            ProfileType.PARSER: "parser_profile",
            ProfileType.CHUNKING: "chunking_profile",
            ProfileType.EMBEDDING: "embedding_profile",
            ProfileType.RETRIEVAL: "retrieval_profile",
            ProfileType.ROUTING: "routing_profile",
            ProfileType.RERANKER: "reranker_profile",
            ProfileType.GENERATION: "generation_profile",
            ProfileType.INGESTION_PRESET: "parser_profile",
            ProfileType.RETRIEVAL_PRESET: "retrieval_profile",
        }[profile_type]
        profile_id = profile_id or new_resource_id(id_kind)
        version = await self.repository.next_profile_version(owner_scope_id, profile_id)
        validated = self.validate_profile_config(profile_type, config)
        profile = ProfileVersion(
            profile_id=profile_id,
            profile_type=profile_type,
            name=name,
            version=version,
            strategy=strategy,
            config=validated,
            description=description,
            based_on_preset=based_on_preset,
            workspace_id=workspace_for_scope(owner_scope_id),
            owner_scope_id=owner_scope_id,
        )
        return await self.repository.save_profile(profile)

    @staticmethod
    def validate_profile_config(profile_type: ProfileType, config: dict[str, Any]) -> dict[str, Any]:
        """Validate the profile config.

        Args:
            profile_type (ProfileType): The profile type.
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            dict[str, Any]: The profile config.
        """
        model = {
            ProfileType.PARSER: ParserProfileConfig,
            ProfileType.CHUNKING: ChunkingProfileConfig,
            ProfileType.EMBEDDING: EmbeddingProfileConfig,
            ProfileType.RETRIEVAL: RetrievalProfileConfig,
            ProfileType.ROUTING: RetrievalRoutingProfileConfig,
            ProfileType.GENERATION: GenerationProfileConfig,
        }.get(profile_type)
        return model.model_validate(config).model_dump(mode="json") if model else config

    async def create_index(
        self,
        *,
        owner_scope_id: str,
        collection_id: str,
        parser_profile_id: str,
        parser_profile_version: int,
        chunking_profile_id: str,
        chunking_profile_version: int,
        embedding_profile_id: str,
        embedding_profile_version: int,
    ) -> IndexVersion:
        """Create the index.

        Args:
            owner_scope_id (str): The owner scope id.
            collection_id (str): Knowledge collection identifier.
            parser_profile_id (str): The parser profile id.
            parser_profile_version (int): The parser profile version.
            chunking_profile_id (str): The chunking profile id.
            chunking_profile_version (int): The chunking profile version.
            embedding_profile_id (str): The embedding profile id.
            embedding_profile_version (int): The embedding profile version.

        Returns:
            IndexVersion: The index.
        """
        await self.repository.get_collection(owner_scope_id, collection_id)
        parser = await self.repository.get_profile(owner_scope_id, parser_profile_id, parser_profile_version, ProfileType.PARSER)
        chunking = await self.repository.get_profile(owner_scope_id, chunking_profile_id, chunking_profile_version, ProfileType.CHUNKING)
        embedding = await self.repository.get_profile(owner_scope_id, embedding_profile_id, embedding_profile_version, ProfileType.EMBEDDING)
        existing = await self.repository.list_indexes(owner_scope_id, collection_id)
        fingerprint = hashlib.sha256(
            f"{embedding.config.get('provider')}:{embedding.config.get('model')}:{embedding.config.get('dimensions')}".encode()
        ).hexdigest()[:12]
        index_id = new_resource_id("index")
        index = IndexVersion(
            index_id=index_id,
            workspace_id=workspace_for_scope(owner_scope_id),
            owner_scope_id=owner_scope_id,
            collection_id=collection_id,
            version=max((item.version for item in existing), default=0) + 1,
            parser_profile_id=parser.profile_id,
            parser_profile_version=parser.version,
            chunking_profile_id=chunking.profile_id,
            chunking_profile_version=chunking.version,
            embedding_profile_id=embedding.profile_id,
            embedding_profile_version=embedding.version,
            embedding_fingerprint=fingerprint,
            # Embeddings with different models/dimensions must not share one
            # physical vector index. Logical collections remain stable while
            # this adapter-level name may change between index versions.
            physical_collection=f"DocumentChunk_{fingerprint}",
            physical_collection_id=collection_id,
            physical_index_id=index_id,
        )
        return await self.repository.save_index(index)

    async def activate_index(self, *, owner_scope_id: str, collection_id: str, index_id: str) -> CollectionResource:
        """Compute the activate index.

        Args:
            owner_scope_id (str): The owner scope id.
            collection_id (str): Knowledge collection identifier.
            index_id (str): The index id.

        Returns:
            CollectionResource: The index.
        """
        collection = await self.repository.get_collection(owner_scope_id, collection_id)
        index = await self.repository.get_index(owner_scope_id, index_id)
        if index.collection_id != collection.collection_id:
            raise ResourceConflictError("index does not belong to collection")
        if index.status not in {ResourceStatus.READY, ResourceStatus.ACTIVE}:
            raise ResourceConflictError("only a ready index can be activated")
        for previous in await self.repository.list_indexes(owner_scope_id, collection_id):
            if previous.index_id != index.index_id and previous.status == ResourceStatus.ACTIVE:
                previous.status = ResourceStatus.READY
                await self.repository.save_index(previous)
        index.status = ResourceStatus.ACTIVE
        index.activated_at = datetime.now(timezone.utc)
        await self.repository.save_index(index)
        collection.active_index_id = index.index_id
        collection.status = ResourceStatus.READY
        collection.document_count = index.document_count
        collection.chunk_count = index.chunk_count
        return await self.repository.save_collection(collection)

    async def create_rag_agent(
        self,
        *,
        owner_scope_id: str,
        name: str,
        collection_id: str,
        retrieval_profile_id: str,
        generation_profile_id: str,
        routing_profile_id: str | None = None,
        description: str = "",
    ) -> RAGAgentDefinition:
        """Create the rag agent.

        Args:
            owner_scope_id (str): The owner scope id.
            name (str): Workflow or resource name.
            collection_id (str): Knowledge collection identifier.
            retrieval_profile_id (str): The retrieval profile id.
            generation_profile_id (str): The generation profile id.
            routing_profile_id (str | None): The routing profile id (optional, default None).
            description (str): The description (optional, default '').

        Returns:
            RAGAgentDefinition: The rag agent.
        """
        await self.repository.get_collection(owner_scope_id, collection_id)
        retrieval = await self.repository.get_profile(owner_scope_id, retrieval_profile_id, expected_type=ProfileType.RETRIEVAL)
        generation = await self.repository.get_profile(owner_scope_id, generation_profile_id, expected_type=ProfileType.GENERATION)
        routing = None
        if routing_profile_id:
            routing = await self.repository.get_profile(
                owner_scope_id, routing_profile_id, expected_type=ProfileType.ROUTING
            )
            routing_config = RetrievalRoutingProfileConfig.model_validate(routing.config)
            for route in routing_config.routes:
                await self.repository.get_collection(owner_scope_id, route.collection_id)
                await self.repository.get_profile(
                    owner_scope_id,
                    route.retrieval_profile_id,
                    route.retrieval_profile_version,
                    ProfileType.RETRIEVAL,
                )
        agent = RAGAgentDefinition(
            rag_agent_id=new_resource_id("rag_agent"),
            workspace_id=workspace_for_scope(owner_scope_id),
            owner_scope_id=owner_scope_id,
            name=name,
            description=description,
            collection_id=collection_id,
            retrieval_profile_id=retrieval.profile_id,
            retrieval_profile_version=retrieval.version,
            generation_profile_id=generation.profile_id,
            generation_profile_version=generation.version,
            routing_profile_id=routing.profile_id if routing else None,
            routing_profile_version=routing.version if routing else None,
        )
        return await self.repository.save_rag_agent(agent)

    async def ensure_default_profiles(self, owner_scope_id: str) -> dict[str, ProfileVersion]:
        """Ensure the default profiles.

        Args:
            owner_scope_id (str): The owner scope id.

        Returns:
            dict[str, ProfileVersion]: The default profiles.
        """
        defaults: list[tuple[ProfileType, str, str, dict[str, Any]]] = [
            (ProfileType.PARSER, "Standard Parser", "standard", ParserProfileConfig().model_dump()),
            (ProfileType.CHUNKING, "Recursive Structure-Aware", "recursive", ChunkingProfileConfig().model_dump()),
            (ProfileType.EMBEDDING, "Default Semantic Search", "openai", EmbeddingProfileConfig().model_dump()),
            (ProfileType.RETRIEVAL, "Balanced", "hybrid_rerank", RetrievalProfileConfig().model_dump()),
            (ProfileType.GENERATION, "Grounded Answer", "grounded", GenerationProfileConfig().model_dump()),
        ]
        result: dict[str, ProfileVersion] = {}
        existing = await self.repository.list_profiles(owner_scope_id)
        for profile_type, name, strategy, config in defaults:
            match = next((p for p in existing if p.profile_type == profile_type and p.name == name), None)
            if match is None:
                match = await self.create_profile_version(
                    owner_scope_id=owner_scope_id,
                    profile_type=profile_type,
                    name=name,
                    strategy=strategy,
                    config=config,
                )
            result[profile_type.value] = match
        return result
