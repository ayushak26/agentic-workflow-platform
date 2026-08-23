"""RAGAgent: hybrid retrieval + grounded generation with citations.

Reuses the Phase 3 retrieval pipeline (hybrid search, rerank, compress) via
the `retriever` service. The node owns the grounded-generation step and the
citation parsing — that's the value it adds on top of raw retrieval."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.cost_ledger import CostLedger
from app.retrieval.models import RetrievalFilters, RetrievalQuery


class RAGConfig(BaseModel):
    """Pydantic model defining the RAGConfig shape.

    Attributes:
        rag_agent_id (str | None).
        model (str | None).
        query (str).
        runtime_context (dict[str, Any] | None).
        runtime_filters (dict[str, Any]).
        filters (dict[str, Any]).
        top_k_candidates (int).
        top_n_final (int).
    """
    rag_agent_id: str | None = Field(
        default=None,
        description="Saved RAG Agent resource. New workflows should set this.",
        json_schema_extra={"x-resource": "rag_agent", "x-preferred": True},
    )
    model: str | None = None
    query: str                                  # templated, resolved by runtime
    runtime_context: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Additional runtime info to accompany the query (e.g. a customer "
            "record from an upstream node). Not indexed, not used for "
            "retrieval filtering — kept distinct from retrieved knowledge."
        ),
    )
    runtime_filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional typed metadata filters; security scope is never overridable.",
    )
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k_candidates: int = 25
    top_n_final: int = 8
    alpha: float = 0.5
    rerank: bool = True
    compress: bool = True
    generation_prompt: str = (
        "Answer the question using ONLY the provided sources. Cite every "
        "factual claim with the source's [N] label. If the sources don't "
        "contain the answer, say so."
    )

    @model_validator(mode="after")
    def reject_ambiguous_saved_agent_config(self) -> "RAGConfig":
        """Compute the reject ambiguous saved agent config.

        Returns:
            'RAGConfig': The ambiguous saved agent config.
        """
        if self.rag_agent_id:
            changed_legacy = (
                self.filters
                or self.top_k_candidates != 25
                or self.top_n_final != 8
                or self.alpha != 0.5
                or self.rerank is not True
                or self.compress is not True
            )
            if changed_legacy:
                raise ValueError(
                    "rag_agent_id cannot be combined with legacy retrieval knobs; "
                    "change the saved Retrieval Profile instead"
                )
        return self


class RAGInput(BaseModel):
    """Pydantic model defining the RAGInput shape."""
    pass


class Citation(BaseModel):
    """Pydantic model defining the Citation shape.

    Attributes:
        label (int).
        chunk_id (str).
        source_doc (str).
        snippet (str).
        display_number (int | None).
    """
    label: int                                  # the [N] used in the answer
    chunk_id: str
    source_doc: str
    snippet: str
    display_number: int | None = None           # global registry [N]


class RAGOutput(BaseModel):
    """Pydantic model defining the RAGOutput shape.

    Attributes:
        query (str).
        answer (str).
        citations (list[Citation]).
        sources (list[dict]).
        relevant_context (list[dict]).
        answering_model (str).
        resolved_answering_model (str).
        retrievals (list[dict]).
    """
    query: str = ""
    answer: str
    citations: list[Citation]
    sources: list[dict] = Field(default_factory=list)          # deduplicated per source document (§37)
    relevant_context: list[dict] = Field(default_factory=list)  # content/score per retrieved chunk (§15)
    answering_model: str = ""            # configured value, e.g. "auto"
    resolved_answering_model: str = ""   # concrete model actually used
    retrievals: list[dict]                      # full RetrievedChunk dump for the Cockpit
    rewritten_query: str | None
    grounding_for_drafter: str = ""
    retrieval_trace_id: str = ""
    collection_id: str = ""
    resolved_index_id: str = ""
    rag_agent_id: str = ""
    retrieval_profile_id: str = ""
    generation_profile_id: str = ""
    candidate_count: int = 0
    context_count: int = 0
    timings_ms: dict[str, float] = Field(default_factory=dict)
    resolved_resources: dict[str, Any] = Field(default_factory=dict)
    generation: dict[str, Any] = Field(default_factory=dict)


CITATION_RE = re.compile(r"\[(\d+)\]")


@NodeRegistry.register
class RAGAgent(NodeType):
    """Workflow node type implementing the RAGAgent capability."""
    type_name = "RAGAgent"
    description = "Hybrid retrieval + grounded answer with citations."
    input_schema = RAGInput
    output_schema = RAGOutput
    config_schema = RAGConfig

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        if config.get("rag_agent_id"):
            return {"llm", "cost_ledger", "rag_service"}
        return {"llm", "cost_ledger", "retriever"}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = RAGConfig(**resolved_config)
        llm = self.services["llm"]

        if cfg.rag_agent_id:
            response = await self.services["rag_service"].query(
                owner_scope_id=state["session_id"],
                rag_agent_id=cfg.rag_agent_id,
                query=cfg.query,
                runtime_filters=cfg.runtime_filters,
                runtime_context=cfg.runtime_context,
                llm=llm,
            )
            grounding_for_drafter = "\n\n".join(
                f"[{index}] (source: {chunk.get('doc_title', '')})\n"
                f"{chunk.get('compressed_text') or chunk.get('context_content') or chunk.get('text', '')}"
                for index, chunk in enumerate(response.retrieved_chunks, start=1)
            )
            return {
                "query": response.query,
                "answer": response.answer,
                "citations": [
                    {
                        "label": citation.label,
                        "chunk_id": citation.chunk_id,
                        "source_doc": citation.filename,
                        "snippet": citation.snippet,
                        "display_number": None,
                    }
                    for citation in response.citations
                ],
                "sources": response.sources,
                "relevant_context": response.relevant_context,
                "answering_model": response.configured_answering_model,
                "resolved_answering_model": str(response.generation.get("model") or ""),
                "retrievals": response.retrieved_chunks,
                "rewritten_query": None,
                "grounding_for_drafter": grounding_for_drafter,
                "retrieval_trace_id": response.retrieval_trace_id,
                "collection_id": response.collection_id,
                "resolved_index_id": response.index_id,
                "rag_agent_id": response.rag_agent_id,
                "retrieval_profile_id": response.retrieval_profile_id,
                "generation_profile_id": response.generation_profile_id,
                "candidate_count": response.candidate_count,
                "context_count": response.context_count,
                "timings_ms": response.timings_ms,
                "resolved_resources": response.resolved_resources,
                "generation": response.generation,
            }

        retriever = self.services["retriever"]  # callable: RetrievalQuery -> RetrievalResult

        # 1. Build the retrieval request. session_id always comes from state —
        #    the workflow can't override its own isolation boundary.
        filters_payload = {**cfg.filters, "session_id": state["session_id"], "collection_id": state["collection_id"],}
        q = RetrievalQuery(
            query=cfg.query,
            filters=RetrievalFilters(**filters_payload),
            top_k_candidates=cfg.top_k_candidates,
            top_n_final=cfg.top_n_final,
            alpha=cfg.alpha,
            rerank=cfg.rerank,
            compress=cfg.compress,
        )

        # 2. Retrieve
        result = await retriever(q, llm=llm)
        if not result.chunks:
            return {
                "query": cfg.query,
                "answer": "No sources matched the query.",
                "citations": [],
                "retrievals": [],
                "rewritten_query": result.rewritten_query,
            }

        # 3. Build the labelled sources block — [1], [2], ...
        sources_block = "\n\n".join(
            f"[{i+1}] (from {c.doc_title})\n{c.compressed_text or c.text}"
            for i, c in enumerate(result.chunks)
        )

        # 4. Generate (grounded). Instruction → system; question + sources → user.
        resp = await llm.complete(
            model=cfg.model or "claude-sonnet-4-5",
            system=cfg.generation_prompt,
            user=f"QUESTION: {cfg.query}\n\nSOURCES:\n{sources_block}",
            temperature=0.2,
            stage="generation",
        )
        answer = resp.text

        # 5. Parse citations — keep only labels that exist in the sources
        valid_labels = {i + 1 for i in range(len(result.chunks))}
        cited = sorted({int(m.group(1)) for m in CITATION_RE.finditer(answer)})
        citations = [
            Citation(
                label=label,
                chunk_id=result.chunks[label - 1].chunk_id,
                source_doc=result.chunks[label - 1].doc_title,
                display_number=result.chunks[label - 1].display_number,
                snippet=(result.chunks[label - 1].text[:200] + "...")
                        if len(result.chunks[label - 1].text) > 200
                        else result.chunks[label - 1].text,
            )
            for label in cited
            if label in valid_labels
        ]

        grounding_for_drafter = "\n\n".join(
            f"[{c.display_number}] (source: {c.doc_title})\n"
            f"{c.compressed_text or c.text}"
            for c in result.chunks
            if c.display_number is not None
        )
        generation_model = str(getattr(resp, "model", None) or cfg.model or "claude-sonnet-4-5")
        input_tokens = int(getattr(resp, "input_tokens", 0) or 0)
        output_tokens = int(getattr(resp, "output_tokens", 0) or 0)

        return {
            "query": cfg.query,
            "answer": answer,
            "citations": [c.model_dump() for c in citations],
            "retrievals": [c.model_dump() for c in result.chunks],
            "rewritten_query": result.rewritten_query,
            "grounding_for_drafter": grounding_for_drafter,
            "retrieval_trace_id": result.retrieval_request_id or "",
            "collection_id": state["collection_id"],
            "resolved_index_id": result.resolved_index_id or "",
            "rag_agent_id": "",
            "retrieval_profile_id": result.retrieval_profile_id or "",
            "generation_profile_id": "",
            "candidate_count": len(result.candidates),
            "context_count": len(result.chunks),
            "timings_ms": result.timings_ms,
            "resolved_resources": result.resolved_resources,
            "generation": {
                "model": generation_model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": CostLedger.calculate(
                    generation_model, input_tokens, output_tokens
                ),
            },
        }
