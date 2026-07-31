"""RAGAgent: hybrid retrieval + grounded generation with citations.

Reuses the Phase 3 retrieval pipeline (hybrid search, rerank, compress) via
the `retriever` service. The node owns the grounded-generation step and the
citation parsing — that's the value it adds on top of raw retrieval."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.retrieval.models import RetrievalFilters, RetrievalQuery


class RAGConfig(BaseModel):
    model: str = "claude-sonnet-4-5"
    query: str                                  # templated, resolved by runtime
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


class RAGInput(BaseModel):
    pass


class Citation(BaseModel):
    label: int                                  # the [N] used in the answer
    chunk_id: str
    source_doc: str
    snippet: str
    display_number: int | None = None           # global registry [N]


class RAGOutput(BaseModel):
    answer: str
    citations: list[Citation]
    retrievals: list[dict]                      # full RetrievedChunk dump for the Cockpit
    rewritten_query: str | None
    grounding_for_drafter: str = ""


CITATION_RE = re.compile(r"\[(\d+)\]")


@NodeRegistry.register
class RAGAgent(NodeType):
    type_name = "RAGAgent"
    description = "Hybrid retrieval + grounded answer with citations."
    input_schema = RAGInput
    output_schema = RAGOutput
    config_schema = RAGConfig

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = RAGConfig(**resolved_config)
        llm = self.services["llm"]
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
            model=cfg.model,
            system=cfg.generation_prompt,
            user=f"QUESTION: {cfg.query}\n\nSOURCES:\n{sources_block}",
            temperature=0.2,
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

        return {
            "answer": answer,
            "citations": [c.model_dump() for c in citations],
            "retrievals": [c.model_dump() for c in result.chunks],
            "rewritten_query": result.rewritten_query,
            "grounding_for_drafter": grounding_for_drafter,
        }