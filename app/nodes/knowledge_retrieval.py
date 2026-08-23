"""One generic retrieval-only workflow capability.

Retrieval strategy choices live in the saved Retrieval Profile.  This is the
only retrieval-only NodeType and complements, rather than duplicates,
``RAGAgent`` generation behavior.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.knowledge.models import ProfileType, ResourceStatus
from app.knowledge.repository import ResourceNotFoundError
from app.nodes.base import NodeType
from app.nodes.contracts import DataType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger
from app.retrieval.filters import coerce_metadata_filter_group
from app.retrieval.models import RetrievalFilters, RetrievalQuery, RetrievalResult
from app.retrieval.service import RetrievalAuthorizationError, RetrievalCompatibilityError


log = get_logger(__name__)


class KnowledgeRetrievalError(RuntimeError):
    """Stable node error safe for workflow/UI handling and operator logs."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class KnowledgeRetrievalConfig(BaseModel):
    """Pydantic model defining the KnowledgeRetrievalConfig shape.

    Attributes:
        collection_id (str).
        retrieval_profile_id (str).
        query (str).
        runtime_filters (dict[str, Any]).
    """
    model_config = ConfigDict(extra="forbid")

    collection_id: str = Field(min_length=1, json_schema_extra={"x-resource": "collection"})
    retrieval_profile_id: str = Field(min_length=1, json_schema_extra={"x-resource": "retrieval_profile"})
    query: str = Field(min_length=1)
    runtime_filters: dict[str, Any] = Field(default_factory=dict)


class KnowledgeRetrievalInput(BaseModel):
    """Pydantic model defining the KnowledgeRetrievalInput shape."""
    pass


class KnowledgeRetrievalOutput(BaseModel):
    """Pydantic model defining the KnowledgeRetrievalOutput shape.

    Attributes:
        retrieved_chunks (list[dict]).
        citations (list[dict]).
        context (str).
        retrieval_trace_id (str).
        collection_id (str).
        resolved_index_id (str).
        retrieval_profile_id (str).
        candidate_count (int).
    """
    retrieved_chunks: list[dict]
    citations: list[dict]
    context: str
    retrieval_trace_id: str
    collection_id: str
    resolved_index_id: str
    retrieval_profile_id: str
    candidate_count: int
    context_count: int
    timings_ms: dict[str, float]
    resolved_resources: dict[str, Any]
    status: str = "success"


@NodeRegistry.register
class KnowledgeRetrieval(NodeType):
    """Workflow node type implementing the KnowledgeRetrieval capability."""
    type_name = "KnowledgeRetrieval"
    description = "Retrieve secured knowledge through a saved Retrieval Profile without generating an answer."
    input_schema = KnowledgeRetrievalInput
    output_schema = KnowledgeRetrievalOutput
    config_schema = KnowledgeRetrievalConfig
    accepts = {DataType.STATE, DataType.TEXT, DataType.JSON}
    produces = {DataType.STATE, DataType.TEXT, DataType.JSON, DataType.LIST}
    permissions = {"knowledge:read"}

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return {"retrieval_service"}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = KnowledgeRetrievalConfig.model_validate(resolved_config)
        scope = state["session_id"]
        await self._validate_resources(cfg, scope)
        metadata = coerce_metadata_filter_group(cfg.runtime_filters)
        # Attribute any LLM-driven retrieval stage (query rewrite, rerank,
        # compression) to this node's own configured collection rather than
        # the run-level default. Without this, RetrievalService.retrieve()
        # falls back to its own unbound gateway (llm=None -> self.llm) and
        # every such call lands in the cost ledger tagged collection_id
        # "default", even though a real collection was queried.
        llm = self.services.get("llm")
        if llm is not None and hasattr(llm, "with_collection_id"):
            llm = llm.with_collection_id(cfg.collection_id)
        request = RetrievalQuery(
            query=cfg.query,
            filters=RetrievalFilters(
                session_id=scope,
                collection_id=cfg.collection_id,
                metadata=metadata,
            ),
            retrieval_profile_id=cfg.retrieval_profile_id,
        )
        started = time.perf_counter()
        log.info(
            "knowledge_retrieval.started",
            node_id=self.node_id,
            collection_id=cfg.collection_id,
            retrieval_profile_id=cfg.retrieval_profile_id,
            query_length=len(cfg.query),
        )
        try:
            result, retry_count = await self._retrieve_with_policy(request, scope, llm)
        except KnowledgeRetrievalError as exc:
            log.warning(
                "knowledge_retrieval.failed",
                node_id=self.node_id,
                collection_id=cfg.collection_id,
                error_code=exc.code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise
        citations = [
            {
                "document_id": chunk.document_id,
                "source_version_id": chunk.source_version_id,
                "chunk_id": chunk.chunk_id,
                "filename": chunk.doc_title,
                "page": chunk.page,
                "section": chunk.section,
                "evidence_status": "retrieved_not_verified",
            }
            for chunk in result.chunks
        ]
        output = {
            "retrieved_chunks": [chunk.model_dump(mode="json") for chunk in result.chunks],
            "citations": citations,
            "context": result.final_context,
            "retrieval_trace_id": result.retrieval_request_id or "",
            "collection_id": cfg.collection_id,
            "resolved_index_id": result.resolved_index_id or "",
            "retrieval_profile_id": cfg.retrieval_profile_id,
            "candidate_count": len(result.candidates),
            "context_count": len(result.chunks),
            "timings_ms": result.timings_ms,
            "resolved_resources": result.resolved_resources,
            "status": "success" if result.chunks else "no_results",
        }
        log.info(
            "knowledge_retrieval.completed",
            node_id=self.node_id,
            collection_id=cfg.collection_id,
            result_count=len(result.chunks),
            candidate_count=len(result.candidates),
            retry_count=retry_count,
            status=output["status"],
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return output

    async def _validate_resources(self, cfg: KnowledgeRetrievalConfig, scope: str) -> None:
        """Revalidate saved references immediately before every execution."""

        repository = self.services.get("knowledge_repository")
        if repository is None:
            # RetrievalService still validates resources. Keep legacy/test
            # dependency injection working while production requires both.
            return
        try:
            collection = await repository.get_collection(scope, cfg.collection_id)
        except ResourceNotFoundError as exc:
            raise KnowledgeRetrievalError(
                "COLLECTION_NOT_FOUND",
                "The selected Knowledge Studio collection does not exist or is not accessible.",
            ) from exc
        if collection.status not in {ResourceStatus.READY, ResourceStatus.ACTIVE}:
            raise KnowledgeRetrievalError(
                "COLLECTION_NOT_READY",
                f"The selected Knowledge Studio collection is {collection.status.value}.",
            )
        if not collection.active_index_id:
            raise KnowledgeRetrievalError(
                "COLLECTION_NOT_READY",
                "The selected Knowledge Studio collection has no active index.",
            )
        try:
            profile = await repository.get_profile(
                scope, cfg.retrieval_profile_id, None, ProfileType.RETRIEVAL
            )
        except ResourceNotFoundError as exc:
            raise KnowledgeRetrievalError(
                "RETRIEVAL_INVALID_REQUEST",
                "The selected retrieval profile does not exist or is not accessible.",
            ) from exc
        if profile.status not in {ResourceStatus.READY, ResourceStatus.ACTIVE}:
            raise KnowledgeRetrievalError(
                "RETRIEVAL_INVALID_REQUEST",
                f"The selected retrieval profile is {profile.status.value}.",
            )

    async def _retrieve_with_policy(self, request, scope: str, llm: Any):
        """Bound retrieval and retry only recognized transient failures once."""

        service = self.services.get("retrieval_service")
        if service is None:
            raise KnowledgeRetrievalError(
                "KNOWLEDGE_STUDIO_UNAVAILABLE", "Knowledge retrieval is unavailable."
            )
        for attempt in range(2):
            try:
                async with asyncio.timeout(settings.external_request_timeout_seconds):
                    raw_result = await service.retrieve(
                        request, owner_scope_id=scope, llm=llm
                    )
                    # The retrieval service is an application boundary.  Validate
                    # its response even when an alternate/test/provider adapter is
                    # injected so malformed data cannot crash a downstream node.
                    result = RetrievalResult.model_validate(raw_result)
                    return result, attempt
            except TimeoutError as exc:
                if attempt == 0:
                    await asyncio.sleep(random.uniform(0.05, 0.15))
                    continue
                raise KnowledgeRetrievalError(
                    "RETRIEVAL_TIMEOUT", "Knowledge retrieval exceeded its deadline."
                ) from exc
            except RetrievalAuthorizationError as exc:
                raise KnowledgeRetrievalError(
                    "COLLECTION_ACCESS_DENIED", "The collection is not accessible in this workspace."
                ) from exc
            except ResourceNotFoundError as exc:
                raise KnowledgeRetrievalError(
                    "COLLECTION_NOT_FOUND", "A referenced Knowledge Studio resource no longer exists."
                ) from exc
            except RetrievalCompatibilityError as exc:
                raise KnowledgeRetrievalError("RETRIEVAL_INVALID_REQUEST", str(exc)) from exc
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                transient = status_code == 429 or (
                    isinstance(status_code, int) and 500 <= status_code < 600
                ) or isinstance(exc, (ConnectionError, OSError))
                if transient and attempt == 0:
                    await asyncio.sleep(random.uniform(0.05, 0.15))
                    continue
                code = "RETRIEVAL_RATE_LIMITED" if status_code == 429 else (
                    "KNOWLEDGE_STUDIO_UNAVAILABLE" if transient else "RETRIEVAL_ERROR"
                )
                raise KnowledgeRetrievalError(code, "Knowledge retrieval failed.") from exc
        raise AssertionError("unreachable")
