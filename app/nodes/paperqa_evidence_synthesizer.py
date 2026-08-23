"""Per-document, gap-aware literature synthesis over already-acquired sources.

This node never fetches anything itself. PaperQA2's own ``Docs.aadd_url``
performs an ungoverned raw HTTP fetch with no SSRF guard, size/redirect caps,
or CrossRef canonical/retraction check — bypassing every safety guarantee
:class:`~app.nodes.research_source_acquirer.ResearchSourceAcquirer` already
provides. So this node only ever calls ``Docs.aadd_file`` with bytes already
downloaded and stored by that acquirer, never ``aadd_url``.

Its output is a candidate literature synthesis, not verified evidence. Only
``ProposalEvidenceFactoryAgent`` (or ``ClaimEvidenceVerifier``) may promote a
claim to a verified status; this node does not write to the proposal graph.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from io import BytesIO
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.evidence.models import FullTextDocument, coerce_typed_list_field
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.state import proposal_graph_from_state


class PaperQAEvidenceSynthesizerInput(BaseModel):
    """Pydantic model defining the PaperQAEvidenceSynthesizerInput shape."""
    pass


class PaperQAEvidenceSynthesizerConfig(BaseModel):
    """Pydantic model defining the PaperQAEvidenceSynthesizerConfig shape.

    Attributes:
        documents (str | list[FullTextDocument]).
        llm_model (str).
        summary_llm_model (str).
        embedding_model (str).
        evidence_k (int).
        max_claims (int).
        max_documents_per_claim (int).
    """
    documents: str | list[FullTextDocument]
    llm_model: str = "gpt-5.6-luna"
    summary_llm_model: str = "gpt-5.6-luna"
    embedding_model: str = "text-embedding-3-small"
    evidence_k: int = Field(default=10, ge=1, le=30)
    max_claims: int = Field(default=15, ge=1, le=50)
    max_documents_per_claim: int = Field(default=12, ge=1, le=30)

    @field_validator("documents", mode="before")
    @classmethod
    def _coerce_documents(cls, value: Any) -> Any:
        """Internal helper for the coerce documents step.

        Args:
            value (Any): Value to process.

        Returns:
            Any: The documents.
        """
        return coerce_typed_list_field(value, FullTextDocument, "documents")


class DocumentCoverage(BaseModel):
    """Pydantic model defining the DocumentCoverage shape.

    Attributes:
        document_id (str).
        citation (str).
        used (bool).
        best_score (int).
    """
    document_id: str
    citation: str
    used: bool
    best_score: int = -1


class ClaimSynthesis(BaseModel):
    """Pydantic model defining the ClaimSynthesis shape.

    Attributes:
        claim_id (str).
        question (str).
        answer (str).
        formatted_answer (str).
        documents_total (int).
        documents_used (int).
        document_coverage (list[DocumentCoverage]).
        cost_usd (float).
    """
    claim_id: str
    question: str
    answer: str = ""
    formatted_answer: str = ""
    documents_total: int = 0
    documents_used: int = 0
    document_coverage: list[DocumentCoverage] = Field(default_factory=list)
    cost_usd: float = 0.0
    error: str | None = None


class PaperQAEvidenceSynthesizerOutput(BaseModel):
    """Pydantic model defining the PaperQAEvidenceSynthesizerOutput shape.

    Attributes:
        results (list[ClaimSynthesis]).
        claims_processed (int).
        claims_skipped_no_claim_text (int).
        total_cost_usd (float).
        verification_status (str).
        note (str).
    """
    results: list[ClaimSynthesis] = Field(default_factory=list)
    claims_processed: int = 0
    claims_skipped_no_claim_text: int = 0
    total_cost_usd: float = 0.0
    verification_status: str = "unverified_synthesis"
    note: str = (
        "PaperQA2 output is a candidate literature synthesis, not verified "
        "evidence. Only ProposalEvidenceFactoryAgent's exact-passage check "
        "may promote a claim to verified status."
    )


@NodeRegistry.register
class PaperQAEvidenceSynthesizerAgent(NodeType):
    """Workflow node type implementing the PaperQAEvidenceSynthesizerAgent capability."""
    type_name = "PaperQAEvidenceSynthesizerAgent"
    description = (
        "Run PaperQA2 over already-acquired full-text documents, per claim, "
        "for per-document coverage and gap-aware literature synthesis. Never "
        "fetches sources itself (no aadd_url) and never certifies evidence."
    )
    input_schema = PaperQAEvidenceSynthesizerInput
    config_schema = PaperQAEvidenceSynthesizerConfig
    output_schema = PaperQAEvidenceSynthesizerOutput

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return {"object_store"}

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the result.

        Args:
            state (dict[str, Any]): Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = PaperQAEvidenceSynthesizerConfig(**resolved_config)
        if isinstance(cfg.documents, str):
            raise ValueError(
                "documents template did not resolve to a document list"
            )
        store = self.services.get("object_store")
        if store is None:
            raise RuntimeError(
                "PaperQAEvidenceSynthesizerAgent requires object_store"
            )

        import paperqa

        graph = proposal_graph_from_state(state)
        by_claim: dict[str, list[FullTextDocument]] = defaultdict(list)
        for document in cfg.documents:
            by_claim[document.claim_id].append(document)

        settings = paperqa.Settings(
            llm=cfg.llm_model,
            summary_llm=cfg.summary_llm_model,
            embedding=cfg.embedding_model,
        )
        settings.answer.evidence_k = cfg.evidence_k

        results: list[ClaimSynthesis] = []
        skipped_no_text = 0
        claim_ids = list(by_claim)[: cfg.max_claims]
        for claim_id in claim_ids:
            claim = graph.claims.get(claim_id)
            if claim is None or not claim.text.strip():
                skipped_no_text += 1
                continue
            documents = by_claim[claim_id][: cfg.max_documents_per_claim]
            results.append(
                await self._synthesize_claim(
                    claim_id=claim_id,
                    question=claim.text,
                    documents=documents,
                    store=store,
                    settings=settings,
                    paperqa=paperqa,
                )
            )

        return PaperQAEvidenceSynthesizerOutput(
            results=results,
            claims_processed=len(results),
            claims_skipped_no_claim_text=skipped_no_text,
            total_cost_usd=sum(item.cost_usd for item in results),
        ).model_dump(mode="json")

    async def _synthesize_claim(
        self,
        *,
        claim_id: str,
        question: str,
        documents: list[FullTextDocument],
        store: Any,
        settings: Any,
        paperqa: Any,
    ) -> ClaimSynthesis:
        """Internal helper for the synthesize claim step.

        Args:
            claim_id (str): The claim id.
            question (str): Question text.
            documents (list[FullTextDocument]): The documents.
            store (Any): Store instance.
            settings (Any): Application settings.
            paperqa (Any): The paperqa.

        Returns:
            ClaimSynthesis: The claim.
        """
        try:
            docs = paperqa.Docs()
            for document in documents:
                raw = await asyncio.to_thread(
                    store.get_bytes,
                    document.pdf_object_key,
                )
                await docs.aadd_file(
                    BytesIO(raw),
                    citation=document.citation,
                    docname=document.document_id,
                    dockey=document.document_id,
                    title=document.title,
                    settings=settings,
                )
            session = await docs.aquery(question, settings=settings)
            used_document_ids = {
                context.text.doc.dockey
                for context in session.contexts
                if context.score > 0
            }
            coverage = [
                DocumentCoverage(
                    document_id=document.document_id,
                    citation=document.citation,
                    used=document.document_id in used_document_ids,
                    best_score=max(
                        (
                            context.score
                            for context in session.contexts
                            if context.text.doc.dockey == document.document_id
                        ),
                        default=-1,
                    ),
                )
                for document in documents
            ]
            return ClaimSynthesis(
                claim_id=claim_id,
                question=question,
                answer=session.answer,
                formatted_answer=session.formatted_answer,
                documents_total=len(documents),
                documents_used=len(used_document_ids),
                document_coverage=coverage,
                cost_usd=session.cost,
            )
        except Exception as exc:
            return ClaimSynthesis(
                claim_id=claim_id,
                question=question,
                documents_total=len(documents),
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
