"""Resolve a saved RAG Agent, retrieve securely, then generate an answer."""
from __future__ import annotations

import re
import time
from typing import Any

import json

from app.knowledge.models import (
    GenerationProfileConfig,
    ProfileType,
    ResourceStatus,
    RetrievalRoute,
    RetrievalRoutingProfileConfig,
)
from app.knowledge.repository import KnowledgeRepository
from app.llm.model_catalog import AUTO_MODEL
from app.observability.cost_ledger import CostLedger
from app.rag.models import RAGCitation, RAGQueryResponse
from app.retrieval.filters import coerce_metadata_filter_group
from app.retrieval.models import MetadataFilterGroup, RetrievalFilters, RetrievalQuery

CITATION_RE = re.compile(r"\[(\d+)\]")


def _build_sources(
    citations: list[RAGCitation], chunk_by_id: dict[str, Any]
) -> list[dict[str, Any]]:
    """Group citations into one entry per source document (§37 dedup shape).

    Forwards whatever provenance a chunk's metadata dict already carries
    (e.g. source_uri) verbatim — no per-connector field mapping here, so a
    future knowledge source type needs no change in this function.
    """
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for citation in citations:
        key = citation.document_id or citation.filename
        if key not in grouped:
            order.append(key)
            chunk = chunk_by_id.get(citation.chunk_id)
            grouped[key] = {
                "file_name": citation.filename,
                "document_id": citation.document_id,
                "source_id": citation.source_id,
                "source_version_id": citation.source_version_id,
                "metadata": dict(chunk.metadata) if chunk is not None else {},
                "locations": [],
            }
        grouped[key]["locations"].append(
            {"page": citation.page, "section": citation.section}
        )
    return [grouped[key] for key in order]


def _build_relevant_context(chunks: list[Any]) -> list[dict[str, Any]]:
    """One entry per retrieved chunk actually used to ground the answer."""
    result = []
    for chunk in chunks:
        content = chunk.compressed_text or chunk.context_content or chunk.text
        score = chunk.rerank_score if chunk.rerank_score is not None else chunk.hybrid_score
        result.append(
            {
                "content": content,
                "score": score,
                "file_name": chunk.doc_title,
                "page_no": chunk.page,
                "section": chunk.section,
            }
        )
    return result


class RAGService:
    """Provides the RAGService behaviour."""
    def __init__(self, *, repository: KnowledgeRepository, retrieval_service: Any, llm: Any):
        """Initialize the RAGService.

        Args:
            repository (KnowledgeRepository): The repository.
            retrieval_service (Any): The retrieval service.
            llm (Any): The llm.
        """
        self.repository = repository
        self.retrieval_service = retrieval_service
        self.llm = llm

    async def _select_route(
        self,
        config: RetrievalRoutingProfileConfig,
        query: str,
        llm: Any,
    ) -> tuple[RetrievalRoute, str]:
        """Select the route.

        Args:
            config (RetrievalRoutingProfileConfig): Node configuration mapping.
            query (str): Query filter.
            llm (Any): The llm.

        Returns:
            tuple[RetrievalRoute, str]: The route.
        """
        normalized = query.casefold()
        scored = [
            (
                sum(1 for keyword in route.keywords if keyword.casefold() in normalized),
                route,
            )
            for route in config.routes
        ]
        deterministic = max(scored, key=lambda item: item[0])
        if config.mode == "deterministic" or (
            config.mode == "hybrid" and deterministic[0] > 0
        ):
            if deterministic[0] > 0:
                return deterministic[1], "deterministic_keyword"
        if config.mode in {"ai", "hybrid"}:
            router_llm = llm.ensure_routed(node_type="RAGAgent") if hasattr(llm, "ensure_routed") else llm
            response = await router_llm.complete(
                model=AUTO_MODEL,
                system=(
                    "Choose exactly one knowledge route for the question. Return only its "
                    "route_id. Routes: "
                    + "; ".join(
                        f"{route.route_id}: {route.name} — {route.description}"
                        for route in config.routes
                    )
                ),
                user=query,
                temperature=0.0,
            )
            selected = response.text.strip()
            match = next(
                (route for route in config.routes if route.route_id == selected), None
            )
            if match is not None:
                return match, "ai_classification"
        fallback_id = config.default_route_id
        fallback = next(
            (route for route in config.routes if route.route_id == fallback_id),
            config.routes[0],
        )
        return fallback, "configured_default"

    async def query(
        self,
        *,
        owner_scope_id: str,
        rag_agent_id: str,
        query: str,
        runtime_filters: dict[str, Any] | MetadataFilterGroup | None = None,
        document_ids: list[str] | None = None,
        runtime_context: dict[str, Any] | None = None,
        llm: Any | None = None,
    ) -> RAGQueryResponse:
        """Query the result.

        Args:
            owner_scope_id (str): The owner scope id.
            rag_agent_id (str): The rag agent id.
            query (str): Query filter.
            runtime_filters (dict[str, Any] | MetadataFilterGroup | None): The runtime filters (optional, default None).
            runtime_context (dict[str, Any] | None): The runtime context (optional, default None).
            llm (Any | None): The llm (optional, default None).

        Returns:
            RAGQueryResponse: The result.
        """
        agent = await self.repository.get_rag_agent(owner_scope_id, rag_agent_id)
        if agent.status != ResourceStatus.ACTIVE:
            raise ValueError(f"RAG agent {rag_agent_id} is not active")
        gateway = llm or self.llm
        routing_profile = None
        selected_route = None
        routing_reason = None
        collection_id = agent.collection_id
        retrieval_profile_id = agent.retrieval_profile_id
        retrieval_profile_version = agent.retrieval_profile_version
        if agent.routing_profile_id:
            routing_profile = await self.repository.get_profile(
                owner_scope_id,
                agent.routing_profile_id,
                agent.routing_profile_version,
                ProfileType.ROUTING,
            )
            routing_config = RetrievalRoutingProfileConfig.model_validate(
                routing_profile.config
            )
            selected_route, routing_reason = await self._select_route(
                routing_config, query, gateway
            )
            collection_id = selected_route.collection_id
            retrieval_profile_id = selected_route.retrieval_profile_id
            retrieval_profile_version = selected_route.retrieval_profile_version
        collection = await self.repository.get_collection(owner_scope_id, collection_id)
        if collection.status not in {ResourceStatus.READY, ResourceStatus.ACTIVE}:
            raise ValueError(f"collection {collection.collection_id} is not ready")
        if not collection.active_index_id:
            raise ValueError(f"collection {collection.collection_id} has no active index")
        index = await self.repository.get_index(owner_scope_id, collection.active_index_id)
        # Attribute retrieval + generation cost to the RAG Agent's actual
        # collection rather than whatever run-level default `gateway` arrived
        # with — otherwise every RAG Agent query's cost lands in the ledger
        # tagged collection_id "default" regardless of which real collection
        # was queried.
        if hasattr(gateway, "with_collection_id"):
            gateway = gateway.with_collection_id(collection.collection_id)
        retrieval_profile = await self.repository.get_profile(
            owner_scope_id,
            retrieval_profile_id,
            retrieval_profile_version,
            ProfileType.RETRIEVAL,
        )
        generation_profile = await self.repository.get_profile(
            owner_scope_id,
            agent.generation_profile_id,
            agent.generation_profile_version,
            ProfileType.GENERATION,
        )
        generation = GenerationProfileConfig.model_validate(generation_profile.config)
        metadata_group = coerce_metadata_filter_group(runtime_filters)
        request = RetrievalQuery(
            query=query,
            filters=RetrievalFilters(
                session_id=owner_scope_id,
                collection_id=collection.collection_id,
                document_ids=document_ids or None,
                metadata=metadata_group,
            ),
            index_id=index.index_id,
            retrieval_profile_id=retrieval_profile.profile_id,
            retrieval_profile_version=retrieval_profile.version,
            rag_agent_id=agent.rag_agent_id,
        )
        retrieval = await self.retrieval_service.retrieve(
            request, owner_scope_id=owner_scope_id, llm=gateway
        )
        generation_started = time.perf_counter()
        response = None
        if not retrieval.chunks:
            if generation.no_answer_policy == "return_empty":
                answer = ""
            elif generation.no_answer_policy == "request_clarification":
                answer = "I could not find supporting sources. Please clarify the question."
            else:
                answer = "No supporting information was found in the selected knowledge collection."
        else:
            gen_gateway = (
                gateway.ensure_routed(node_type="RAGAgent")
                if generation.model == AUTO_MODEL and hasattr(gateway, "ensure_routed")
                else gateway
            )
            user_prompt = f"QUESTION:\n{query}\n\n"
            if runtime_context:
                user_prompt += f"RUNTIME CONTEXT:\n{json.dumps(runtime_context)}\n\n"
            user_prompt += f"SOURCES:\n{retrieval.final_context}"
            response = await gen_gateway.complete(
                model=generation.model,
                system=generation.instruction,
                user=user_prompt,
                temperature=generation.temperature,
                stage="generation",
            )
            answer = response.text
        generation_ms = (time.perf_counter() - generation_started) * 1000

        labels = sorted({int(match.group(1)) for match in CITATION_RE.finditer(answer)})
        valid = {index + 1 for index in range(len(retrieval.chunks))}
        citations = []
        for label in labels:
            if label not in valid:
                continue
            chunk = retrieval.chunks[label - 1]
            citations.append(
                RAGCitation(
                    label=label,
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    source_id=chunk.source_id,
                    source_version_id=chunk.source_version_id,
                    filename=chunk.doc_title,
                    page=chunk.page,
                    section=chunk.section,
                    snippet=chunk.text[:240] + ("…" if len(chunk.text) > 240 else ""),
                )
            )
        if generation.citation_policy == "required" and retrieval.chunks and not citations:
            # The answer remains visible for debugging, but is explicitly
            # marked unsupported rather than manufacturing a citation.
            answer = f"{answer}\n\nCitation requirement was not satisfied by the generated answer."

        timings = dict(retrieval.timings_ms)
        timings["generation_ms"] = generation_ms
        timings["total_with_generation_ms"] = timings.get("total_ms", 0.0) + generation_ms
        response_model = (
            str(getattr(response, "model", None) or generation.model)
            if response is not None
            else generation.model
        )
        response_input_tokens = int(getattr(response, "input_tokens", 0) or 0)
        response_output_tokens = int(getattr(response, "output_tokens", 0) or 0)
        cache_creation_tokens = int(
            getattr(response, "cache_creation_input_tokens", 0) or 0
        )
        cache_read_tokens = int(getattr(response, "cache_read_input_tokens", 0) or 0)
        generation_usage = (
            {
                "model": response_model,
                "input_tokens": response_input_tokens,
                "output_tokens": response_output_tokens,
                "cost_usd": CostLedger.calculate(
                    response_model,
                    response_input_tokens,
                    response_output_tokens,
                    cache_creation_input_tokens=cache_creation_tokens,
                    cache_read_input_tokens=cache_read_tokens,
                ),
            }
            if response is not None
            else {"model": generation.model, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        )
        if retrieval.retrieval_request_id:
            trace = await self.repository.get_trace(
                owner_scope_id, retrieval.retrieval_request_id
            )
            trace.timings_ms = timings
            if response is not None:
                trace.model_usage.append(
                    {
                        "stage": "generation",
                        "model": response_model,
                        "input_tokens": response_input_tokens,
                        "output_tokens": response_output_tokens,
                        "cache_creation_input_tokens": cache_creation_tokens,
                        "cache_read_input_tokens": cache_read_tokens,
                        "cost_usd": CostLedger.calculate(
                            response_model,
                            response_input_tokens,
                            response_output_tokens,
                            cache_creation_input_tokens=cache_creation_tokens,
                            cache_read_input_tokens=cache_read_tokens,
                        ),
                    }
                )
            await self.repository.save_trace(trace)
        chunk_by_id = {chunk.chunk_id: chunk for chunk in retrieval.chunks}
        return RAGQueryResponse(
            request_id=retrieval.retrieval_request_id or "",
            rag_agent_id=agent.rag_agent_id,
            collection_id=collection.collection_id,
            index_id=index.index_id,
            retrieval_profile_id=retrieval_profile.profile_id,
            retrieval_profile_version=retrieval_profile.version,
            generation_profile_id=generation_profile.profile_id,
            generation_profile_version=generation_profile.version,
            query=query,
            answer=answer,
            citations=citations,
            sources=_build_sources(citations, chunk_by_id),
            relevant_context=_build_relevant_context(retrieval.chunks),
            configured_answering_model=generation.model,
            retrieved_chunks=[chunk.model_dump(mode="json") for chunk in retrieval.chunks],
            final_context=retrieval.final_context,
            retrieval_trace_id=retrieval.retrieval_request_id or "",
            candidate_count=len(retrieval.candidates),
            context_count=len(retrieval.chunks),
            timings_ms=timings,
            resolved_resources={
                **retrieval.resolved_resources,
                "rag_agent_id": agent.rag_agent_id,
                "generation_profile_id": generation_profile.profile_id,
                "generation_profile_version": generation_profile.version,
                "routing_profile_id": routing_profile.profile_id if routing_profile else None,
                "routing_profile_version": routing_profile.version if routing_profile else None,
                "selected_route_id": selected_route.route_id if selected_route else None,
                "routing_reason": routing_reason,
            },
            generation=generation_usage,
        )
