"""Full-text, claim-level evidence verification for EU proposals.

The implementation follows the PaperQA2/Valsci design pattern without asking a
single unrestricted model call to perform the whole research workflow:

1. retrieve page-aware passages deterministically;
2. classify one claim/source pair with a typed model output;
3. verify that the quoted passage exists in the fetched source version;
4. apply deterministic independence, metadata, contradiction, and citation
   integrity gates;
5. expose only verified/qualified claims to proposal drafting.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.evidence.models import (
    CandidateSource,
    CitationRegistryEntry,
    ClaimEvidenceLink,
    EvidenceGap,
    EvidencePolicy,
    EvidenceQAReport,
    FullTextDocument,
    ProposalEvidencePackage,
    RejectedCandidate,
    RetrievedPassage,
    SearchAuditRecord,
    VerifiedClaim,
    coerce_typed_list_field,
)
from app.evidence.retrieval import (
    retrieve_passages,
    stable_id,
)
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.evidence_verification import quote_exists_in_source
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import (
    Authority,
    EvidenceRelation,
    EvidenceSource,
    EvidenceStance,
    Status,
)
from app.proposal_graph.state import (
    proposal_graph_from_state,
    proposal_graph_state_update,
)


class PairVerdict(BaseModel):
    stance: str
    confidence: float = Field(ge=0.0, le=1.0)
    exact_quote: str = ""
    reason: str
    qualification: str = ""
    limitation: str = ""


class ProposalEvidenceFactoryInput(BaseModel):
    pass


class ProposalEvidenceFactoryConfig(BaseModel):
    candidates: str | list[CandidateSource]
    documents: str | list[FullTextDocument]
    search_audit: str | list[SearchAuditRecord] = Field(default_factory=list)
    rejected_candidates: str | list[RejectedCandidate] = Field(
        default_factory=list
    )
    policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    model: str = "claude-sonnet-4-5"
    citation_style: str = "numeric_compact"
    max_passages_per_document: int = Field(default=7, ge=1, le=20)

    @field_validator("candidates", mode="before")
    @classmethod
    def _coerce_candidates(cls, value: Any) -> Any:
        return coerce_typed_list_field(value, CandidateSource, "candidates")

    @field_validator("documents", mode="before")
    @classmethod
    def _coerce_documents(cls, value: Any) -> Any:
        return coerce_typed_list_field(value, FullTextDocument, "documents")

    @field_validator("search_audit", mode="before")
    @classmethod
    def _coerce_search_audit(cls, value: Any) -> Any:
        return coerce_typed_list_field(value, SearchAuditRecord, "search_audit")

    @field_validator("rejected_candidates", mode="before")
    @classmethod
    def _coerce_rejected_candidates(cls, value: Any) -> Any:
        return coerce_typed_list_field(
            value, RejectedCandidate, "rejected_candidates"
        )


class ProposalEvidenceFactoryOutput(ProposalEvidencePackage):
    graph_sources_added: int = 0
    graph_relations_added: int = 0
    report: str = ""


def _materiality(claim_type: str) -> str:
    if claim_type in {"problem", "state_of_art", "impact", "method"}:
        return "critical"
    if claim_type in {"policy", "market", "regulatory"}:
        return "important"
    return "contextual"


def _authority(value: str) -> Authority:
    try:
        return Authority(value)
    except ValueError:
        return Authority.UNVERIFIED


def _strength(score: int) -> str:
    if score >= 85:
        return "strong"
    if score >= 70:
        return "moderate"
    if score >= 50:
        return "limited"
    if score >= 25:
        return "weak"
    return "unusable"


def _evidence_score(
    *,
    document: FullTextDocument,
    stance: str,
    confidence: float,
    exact_locator: bool,
    corroborated: bool,
) -> int:
    authority = {
        "official_eu": 20,
        "peer_reviewed": 18,
        "preprint": 10,
        "grey": 8,
        "partner_claim": 8,
    }.get(document.authority, 4)
    entailment = {
        "supports_directly": 30,
        "supports_with_conditions": 23,
        "mixed": 15,
        "contradicts": 25,
        "context_only": 8,
        "insufficient": 0,
        "not_relevant": 0,
        "cannot_assess": 0,
    }.get(stance, 0)
    directness = 15
    locator = 15 if exact_locator else 0
    version_fit = 3
    context_fit = round(max(0.0, min(1.0, confidence)) * 5)
    independence = 10 if corroborated else 0
    score = (
        authority
        + entailment
        + directness
        + locator
        + version_fit
        + context_fit
        + independence
    )
    if document.retraction_status == "retracted":
        return 0
    if not exact_locator:
        score = min(score, 65)
    return max(0, min(100, score))


def _bibtex_key(entry: CitationRegistryEntry) -> str:
    first_author = (
        re.sub(r"[^A-Za-z0-9]", "", entry.authors[0].split()[-1])
        if entry.authors
        else re.sub(r"[^A-Za-z0-9]", "", entry.source_type.title())
    )
    return f"{first_author}{entry.year or 'nd'}{entry.citation_id.replace('CIT-', '')[:4]}"


@NodeRegistry.register
class ProposalEvidenceFactoryAgent(NodeType):
    type_name = "ProposalEvidenceFactoryAgent"
    description = (
        "Verify proposal claims against immutable full-text pages, build an "
        "auditable citation registry, and fail closed on evidence gaps."
    )
    input_schema = ProposalEvidenceFactoryInput
    config_schema = ProposalEvidenceFactoryConfig
    output_schema = ProposalEvidenceFactoryOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = ProposalEvidenceFactoryConfig(**resolved_config)
        templated_fields = {
            "candidates": cfg.candidates,
            "documents": cfg.documents,
            "search_audit": cfg.search_audit,
            "rejected_candidates": cfg.rejected_candidates,
        }
        unresolved = [
            name
            for name, value in templated_fields.items()
            if isinstance(value, str)
        ]
        if unresolved:
            raise ValueError(
                "evidence templates did not resolve for: "
                + ", ".join(unresolved)
            )
        graph = proposal_graph_from_state(state)
        llm = self.services.get("llm")
        store = self.services.get("object_store")
        if llm is None or store is None:
            missing = [
                name
                for name, service in (("llm", llm), ("object_store", store))
                if service is None
            ]
            raise RuntimeError(
                f"ProposalEvidenceFactoryAgent requires services {missing}"
            )

        candidates = {
            item.candidate_id: item for item in cfg.candidates
        }
        documents_by_claim: dict[str, list[FullTextDocument]] = defaultdict(list)
        for document in cfg.documents:
            documents_by_claim[document.claim_id].append(document)

        links: list[ClaimEvidenceLink] = []
        verified_claims: list[VerifiedClaim] = []
        gaps: list[EvidenceGap] = []
        rejected = list(cfg.rejected_candidates)
        source_records: dict[str, EvidenceSource] = {}
        relation_records: dict[str, EvidenceRelation] = {}
        updated_claims = {}
        retained_documents: dict[str, FullTextDocument] = {}

        for claim in graph.claims.values():
            materiality = _materiality(claim.claim_type)
            claim_links: list[ClaimEvidenceLink] = []
            for document in documents_by_claim.get(claim.id, []):
                candidate = candidates.get(document.candidate_id)
                pair = await self._verify_document(
                    llm=llm,
                    store=store,
                    claim_text=claim.text,
                    document=document,
                    candidate=candidate,
                    model=cfg.model,
                    passage_limit=cfg.max_passages_per_document,
                )
                if pair is None:
                    continue
                claim_links.append(pair)

            support_links = [
                link
                for link in claim_links
                if link.stance
                in {"supports_directly", "supports_with_conditions"}
                and link.confidence >= cfg.policy.minimum_support_confidence
            ]
            contradiction_links = [
                link
                for link in claim_links
                if link.stance == "contradicts"
                and link.confidence >= cfg.policy.minimum_support_confidence
            ]
            independent_groups = sorted(
                {
                    self._document(link.document_id, cfg.documents)
                    .independence_group
                    for link in support_links
                }
            )
            contradiction_searched = any(
                item.claim_id == claim.id
                and item.purpose == "contradiction"
                and not item.error
                for item in cfg.search_audit
            )
            metadata_ok = all(
                self._document(link.document_id, cfg.documents)
                .canonical_metadata_validated
                for link in support_links
            )
            retraction_ok = all(
                self._document(link.document_id, cfg.documents)
                .retraction_status
                in {"clear", "corrected"}
                for link in support_links
            )
            required_independent = (
                cfg.policy.minimum_independent_sources_for_critical_claim
                if materiality == "critical"
                else 1
            )

            if contradiction_links and support_links:
                final_status = "mixed"
            elif contradiction_links:
                final_status = "contradicted"
            elif support_links:
                enough_independent = (
                    len(independent_groups) >= required_independent
                )
                conditional_only = all(
                    link.stance == "supports_with_conditions"
                    for link in support_links
                )
                hard_gates = enough_independent and metadata_ok
                if cfg.policy.require_retraction_and_correction_check:
                    hard_gates = hard_gates and retraction_ok
                if (
                    cfg.policy.require_contradiction_search
                    and materiality == "critical"
                ):
                    hard_gates = hard_gates and contradiction_searched
                if hard_gates:
                    final_status = (
                        "verified_with_qualification"
                        if conditional_only
                        else "verified"
                    )
                else:
                    final_status = "partial"
            elif documents_by_claim.get(claim.id):
                final_status = "insufficient"
            else:
                final_status = "not_found"

            # Add corroboration points only after independence is known.
            corroborated = len(independent_groups) >= 2
            scored_links = [
                link.model_copy(
                    update={
                        "evidence_score": _evidence_score(
                            document=self._document(
                                link.document_id,
                                cfg.documents,
                            ),
                            stance=link.stance,
                            confidence=link.confidence,
                            exact_locator=bool(link.locator.get("page")),
                            corroborated=corroborated,
                        )
                    }
                )
                for link in claim_links
            ]
            scored_links = [
                item.model_copy(
                    update={"strength": _strength(item.evidence_score)}
                )
                for item in scored_links
            ]
            links.extend(scored_links)

            citable = (
                final_status in {"verified", "verified_with_qualification"}
            )
            citable_links = [
                link
                for link in scored_links
                if citable
                and link.stance
                in {"supports_directly", "supports_with_conditions"}
                and link.confidence >= cfg.policy.minimum_support_confidence
            ]
            for link in citable_links:
                document = self._document(link.document_id, cfg.documents)
                candidate = candidates.get(document.candidate_id)
                source = self._source_from_document(
                    document,
                    candidate,
                    passage=link.exact_passage,
                )
                source_records[source.id] = source
                retained_documents[document.document_id] = document
                relation = self._relation_from_link(link, document)
                relation_records[relation.id] = relation

            if final_status not in {"verified", "verified_with_qualification"}:
                gap = self._gap_for_claim(
                    claim.id,
                    final_status,
                    materiality,
                    independent_count=len(independent_groups),
                    required_independent=required_independent,
                    contradiction_searched=contradiction_searched,
                    metadata_ok=metadata_ok,
                    retraction_ok=retraction_ok,
                    policy=cfg.policy,
                )
                gaps.append(gap)

            verified_claims.append(
                VerifiedClaim(
                    claim_id=claim.id,
                    original_text=claim.text,
                    atomic_claim=claim.text,
                    claim_type=claim.claim_type,
                    section_id=claim.proposal_section or "",
                    materiality=materiality,
                    evidence_requirement=(
                        f"{required_independent} independent full-text "
                        "source(s), exact locator, contradiction search"
                    ),
                    final_status=final_status,
                    verified_sentence=(
                        claim.text
                        if final_status
                        in {"verified", "verified_with_qualification"}
                        else ""
                    ),
                    source_ids=[
                        source_id
                        for source_id, source in source_records.items()
                        if any(
                            link.source_id == source_id
                            for link in citable_links
                        )
                    ],
                    evidence_link_ids=[
                        item.link_id for item in citable_links
                    ],
                    independence_groups=independent_groups,
                    qualification="; ".join(
                        item.qualification
                        for item in citable_links
                        if item.qualification
                    ),
                    unresolved_issue=(
                        "" if citable else gaps[-1].gap
                    ),
                    human_review_required=(
                        final_status
                        in {"partial", "mixed", "contradicted"}
                        or materiality == "critical"
                    ),
                )
            )
            verification = (
                Status.ADDRESSED
                if final_status == "verified"
                else Status.PARTIAL
                if final_status == "verified_with_qualification"
                else Status.MISSING
            )
            claim_source_ids = [
                self._source_id_for_document(
                    self._document(item.document_id, cfg.documents)
                )
                for item in citable_links
            ]
            updated_claims[claim.id] = claim.model_copy(
                update={
                    "evidence_source_ids": claim_source_ids,
                    "evidence_relation_ids": [
                        self._relation_from_link(
                            item,
                            self._document(item.document_id, cfg.documents),
                        ).id
                        for item in citable_links
                    ],
                    "verification": verification,
                }
            )

        citation_registry = self._citation_registry(
            retained_documents,
            candidates,
            verified_claims,
        )
        citation_number_by_source = {
            entry.source_id: entry.display_number
            for entry in citation_registry
        }
        verified_claims = [
            item.model_copy(
                update={
                    "inline_citation_tokens": [
                        f"[{citation_number_by_source[source_id]}]"
                        for source_id in item.source_ids
                        if source_id in citation_number_by_source
                    ]
                }
            )
            for item in verified_claims
        ]
        cited_markdown = self._cited_markdown(verified_claims)
        bibliography_markdown = "\n".join(
            f"{entry.display_number}. {entry.formatted_citation}"
            for entry in citation_registry
        )
        bibliography_bibtex = "\n\n".join(
            self._bibtex(entry) for entry in citation_registry
        )
        quantitative_registry = self._quantitative_registry(
            verified_claims,
            links,
        )
        qa_report, blockers = self._quality_report(
            policy=cfg.policy,
            verified_claims=verified_claims,
            citation_registry=citation_registry,
            links=links,
            audit=cfg.search_audit,
            rejected=rejected,
            quantitative_registry=quantitative_registry,
        )

        delta = ProposalGraph(
            claims=updated_claims,
            evidence_sources=source_records,
            claim_evidence=relation_records,
        )
        package = ProposalEvidencePackage(
            verified_claims=verified_claims,
            citation_registry=citation_registry,
            claim_evidence_links=links,
            quantitative_evidence_registry=quantitative_registry,
            proposal_ready_cited_markdown=cited_markdown,
            bibliography_markdown=bibliography_markdown,
            bibliography_bibtex=bibliography_bibtex,
            search_audit=cfg.search_audit,
            rejected_candidates=rejected,
            evidence_gaps=gaps,
            qa_report=qa_report,
            blocking_issues=blockers,
        )
        return {
            **package.model_dump(mode="json"),
            "graph_sources_added": len(source_records),
            "graph_relations_added": len(relation_records),
            "report": (
                "VERIFIED / QUALIFIED CLAIMS\n"
                f"{cited_markdown or '[none passed all hard gates]'}\n\n"
                "BIBLIOGRAPHY\n"
                f"{bibliography_markdown or '[none]'}\n\n"
                "BLOCKING ISSUES\n"
                + (
                    "\n".join(f"- {item}" for item in blockers)
                    if blockers
                    else "[none]"
                )
            ),
            "__state__": proposal_graph_state_update(delta),
        }

    async def _verify_document(
        self,
        *,
        llm: Any,
        store: Any,
        claim_text: str,
        document: FullTextDocument,
        candidate: CandidateSource | None,
        model: str,
        passage_limit: int,
    ) -> ClaimEvidenceLink | None:
        raw = await asyncio.to_thread(
            store.get_bytes,
            document.pages_object_key,
        )
        payload = json.loads(raw.decode("utf-8"))
        pages = payload.get("pages") or []
        passages = retrieve_passages(
            claim_text,
            pages,
            document_id=document.document_id,
            limit=passage_limit,
        )
        if not passages:
            return None
        passage_payload = [
            {
                "passage_id": item.passage_id,
                "page": item.page,
                "text": item.text,
            }
            for item in passages
        ]
        verdict = await llm.complete_structured(
            model=model,
            system=(
                "Verify one atomic proposal claim against retrieved passages "
                "from one immutable full-text source. stance must be exactly "
                "supports_directly, supports_with_conditions, mixed, "
                "contradicts, context_only, insufficient, not_relevant, or "
                "cannot_assess. Copy one short exact_quote verbatim from a "
                "supplied passage for supports/contradicts/mixed; otherwise "
                "leave it empty. Do not paraphrase the quote. State material "
                "conditions and limitations."
            ),
            user=(
                f"CLAIM:\n{claim_text}\n\n"
                f"SOURCE TITLE:\n{document.title}\n\n"
                "RETRIEVED PASSAGES (JSON):\n"
                + json.dumps(passage_payload, ensure_ascii=False)
            ),
            response_model=PairVerdict,
            temperature=0.0,
            max_tokens=1000,
        )
        allowed = {
            "supports_directly",
            "supports_with_conditions",
            "mixed",
            "contradicts",
            "context_only",
            "insufficient",
            "not_relevant",
            "cannot_assess",
        }
        stance = (
            verdict.stance
            if verdict.stance in allowed
            else "cannot_assess"
        )
        matched: RetrievedPassage | None = None
        if verdict.exact_quote:
            matched = next(
                (
                    passage
                    for passage in passages
                    if quote_exists_in_source(
                        verdict.exact_quote,
                        passage.text,
                    )
                ),
                None,
            )
        if (
            stance
            in {
                "supports_directly",
                "supports_with_conditions",
                "mixed",
                "contradicts",
            }
            and matched is None
        ):
            stance = "insufficient"
            verdict = verdict.model_copy(
                update={
                    "confidence": 0.0,
                    "exact_quote": "",
                    "reason": (
                        "The verifier quote was not present in the supplied "
                        "source version; the pair failed closed."
                    ),
                }
            )
        matched = matched or passages[0]
        source_id = self._source_id_for_document(document)
        link_id = stable_id(
            "LINK",
            document.claim_id,
            document.document_id,
            matched.passage_id,
            stance,
        )
        initial_score = _evidence_score(
            document=document,
            stance=stance,
            confidence=verdict.confidence,
            exact_locator=bool(matched.page),
            corroborated=False,
        )
        return ClaimEvidenceLink(
            link_id=link_id,
            claim_id=document.claim_id,
            document_id=document.document_id,
            candidate_id=document.candidate_id,
            source_id=source_id,
            passage_id=matched.passage_id,
            exact_passage=verdict.exact_quote,
            locator={
                "page": matched.page,
                "section": matched.section,
                "source_version_id": document.version_id,
                "pages_object_key": document.pages_object_key,
            },
            stance=stance,
            confidence=verdict.confidence,
            evidence_score=initial_score,
            strength=_strength(initial_score),
            qualification=verdict.qualification,
            limitation=verdict.limitation,
            reason=verdict.reason,
            verifier_model=model,
            verified_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _document(
        document_id: str,
        documents: list[FullTextDocument],
    ) -> FullTextDocument:
        for document in documents:
            if document.document_id == document_id:
                return document
        raise KeyError(f"unknown full-text document {document_id}")

    @staticmethod
    def _source_id_for_document(document: FullTextDocument) -> str:
        return stable_id(
            "SRC",
            document.identifier
            or document.canonical_url
            or document.title,
        )

    def _source_from_document(
        self,
        document: FullTextDocument,
        candidate: CandidateSource | None,
        *,
        passage: str,
    ) -> EvidenceSource:
        return EvidenceSource(
            id=self._source_id_for_document(document),
            citation=document.citation,
            identifier=document.identifier,
            authority=_authority(document.authority),
            retrieved_at=document.fetched_at,
            title=document.title,
            source_type=document.source_type,
            version_id=document.version_id,
            content_sha256=document.content_sha256,
            object_key=document.pages_object_key,
            excerpt=passage,
        )

    @staticmethod
    def _relation_from_link(
        link: ClaimEvidenceLink,
        document: FullTextDocument,
    ) -> EvidenceRelation:
        stance = (
            EvidenceStance.SUPPORTS
            if link.stance
            in {"supports_directly", "supports_with_conditions"}
            else EvidenceStance.CONTRADICTS
            if link.stance == "contradicts"
            else EvidenceStance.INSUFFICIENT
        )
        return EvidenceRelation(
            id=stable_id("EV", link.link_id),
            claim_id=link.claim_id,
            source_id=link.source_id,
            source_version_id=document.version_id,
            passage=link.exact_passage,
            locator=f"p. {link.locator.get('page')}",
            passage_sha256=hashlib.sha256(
                link.exact_passage.encode("utf-8")
            ).hexdigest(),
            stance=stance,
            confidence=link.confidence,
            reason=link.reason,
            verifier_model=link.verifier_model,
            verified_at=link.verified_at,
        )

    @staticmethod
    def _gap_for_claim(
        claim_id: str,
        final_status: str,
        materiality: str,
        *,
        independent_count: int,
        required_independent: int,
        contradiction_searched: bool,
        metadata_ok: bool,
        retraction_ok: bool,
        policy: EvidencePolicy,
    ) -> EvidenceGap:
        reasons: list[str] = []
        if independent_count < required_independent:
            reasons.append(
                f"{independent_count}/{required_independent} independent "
                "supporting sources"
            )
        if (
            policy.require_contradiction_search
            and materiality == "critical"
            and not contradiction_searched
        ):
            reasons.append("contradiction search not completed")
        if independent_count and not metadata_ok:
            reasons.append("canonical metadata not validated")
        if (
            independent_count
            and policy.require_retraction_and_correction_check
            and not retraction_ok
        ):
            reasons.append("retraction/correction status not checked")
        if not reasons:
            reasons.append(f"claim status is {final_status}")
        action = (
            "human_review"
            if final_status in {"mixed", "contradicted"}
            else "search_again"
        )
        return EvidenceGap(
            claim_id=claim_id,
            gap="; ".join(reasons),
            proposal_risk=(
                "A critical evaluator-facing claim cannot be cited safely."
                if materiality == "critical"
                else "The claim lacks proposal-grade support."
            ),
            recommended_action=action,
            blocking=materiality == "critical",
        )

    def _citation_registry(
        self,
        retained: dict[str, FullTextDocument],
        candidates: dict[str, CandidateSource],
        claims: list[VerifiedClaim],
    ) -> list[CitationRegistryEntry]:
        claim_ids_by_source: dict[str, list[str]] = defaultdict(list)
        for claim in claims:
            for source_id in claim.source_ids:
                claim_ids_by_source[source_id].append(claim.claim_id)
        entries: list[CitationRegistryEntry] = []
        for number, document in enumerate(
            sorted(retained.values(), key=lambda item: item.title.lower()),
            start=1,
        ):
            candidate = candidates.get(document.candidate_id)
            source_id = self._source_id_for_document(document)
            entries.append(
                CitationRegistryEntry(
                    citation_id=f"CIT-{number:04d}",
                    source_id=source_id,
                    document_id=document.document_id,
                    display_number=number,
                    title=document.title,
                    authors=candidate.authors if candidate else [],
                    year=candidate.year if candidate else None,
                    doi=candidate.doi if candidate else None,
                    canonical_url=document.canonical_url,
                    identifier=document.identifier,
                    source_type=document.source_type,
                    version_id=document.version_id,
                    content_sha256=document.content_sha256,
                    retraction_status=document.retraction_status,
                    formatted_citation=document.citation,
                    claim_ids=sorted(set(claim_ids_by_source[source_id])),
                )
            )
        return entries

    @staticmethod
    def _cited_markdown(claims: list[VerifiedClaim]) -> str:
        lines: list[str] = []
        for claim in claims:
            if claim.final_status in {
                "verified",
                "verified_with_qualification",
            }:
                citation = "".join(claim.inline_citation_tokens)
                sentence = claim.verified_sentence.rstrip()
                if sentence and sentence[-1] in ".!?":
                    sentence = sentence[:-1]
                lines.append(f"- {sentence} {citation}.".strip())
            elif claim.materiality == "critical":
                lines.append(
                    f"- [EVIDENCE GAP {claim.claim_id}: "
                    f"{claim.unresolved_issue}]"
                )
        return "\n".join(lines)

    @staticmethod
    def _bibtex(entry: CitationRegistryEntry) -> str:
        fields = {
            "title": entry.title,
            "author": " and ".join(entry.authors),
            "year": str(entry.year or ""),
            "doi": entry.doi or "",
            "url": entry.canonical_url or "",
        }
        body = ",\n".join(
            f"  {key} = {{{value}}}"
            for key, value in fields.items()
            if value
        )
        return f"@misc{{{_bibtex_key(entry)},\n{body}\n}}"

    @staticmethod
    def _quantitative_registry(
        claims: list[VerifiedClaim],
        links: list[ClaimEvidenceLink],
    ) -> list[dict[str, Any]]:
        registry: list[dict[str, Any]] = []
        for claim in claims:
            numbers = re.findall(r"\b\d+(?:\.\d+)?%?\b", claim.atomic_claim)
            if not numbers:
                continue
            claim_links = [
                link
                for link in links
                if link.claim_id == claim.claim_id
                and link.link_id in claim.evidence_link_ids
            ]
            for index, value in enumerate(numbers, start=1):
                locator = claim_links[0].locator if claim_links else {}
                registry.append(
                    {
                        "evidence_id": (
                            f"NUM-{claim.claim_id}-{index:02d}"
                        ),
                        "claim_ids": [claim.claim_id],
                        "value": value,
                        "unit": "%" if value.endswith("%") else "",
                        "geography": "",
                        "period": "",
                        "population_or_material": "",
                        "number_type": "reported",
                        "source_ids": claim.source_ids,
                        "locator": locator,
                        "formula": "",
                        "inputs": [],
                        "uncertainty": "",
                        "status": (
                            "traceable"
                            if claim.source_ids and locator.get("page")
                            else "incomplete"
                        ),
                    }
                )
        return registry

    @staticmethod
    def _quality_report(
        *,
        policy: EvidencePolicy,
        verified_claims: list[VerifiedClaim],
        citation_registry: list[CitationRegistryEntry],
        links: list[ClaimEvidenceLink],
        audit: list[SearchAuditRecord],
        rejected: list[RejectedCandidate],
        quantitative_registry: list[dict[str, Any]],
    ) -> tuple[EvidenceQAReport, list[str]]:
        critical = [
            item for item in verified_claims if item.materiality == "critical"
        ]
        resolved = [
            item
            for item in verified_claims
            if item.final_status in {
                "verified",
                "verified_with_qualification",
            }
        ]
        qualified = [
            item
            for item in verified_claims
            if item.final_status == "verified_with_qualification"
        ]
        mixed = [
            item
            for item in verified_claims
            if item.final_status in {"mixed", "contradicted"}
        ]
        contradiction_claims = {
            item.claim_id
            for item in audit
            if item.purpose == "contradiction" and not item.error
        }
        exact = [
            item
            for item in links
            if item.exact_passage and item.locator.get("page")
        ]
        registry_sources = {item.source_id for item in citation_registry}
        used_sources = {
            source_id
            for item in verified_claims
            for source_id in item.source_ids
        }
        unused = sorted(registry_sources - used_sources)
        orphan = sorted(used_sources - registry_sources)
        metadata_ok = [
            item
            for item in citation_registry
            if item.identifier and item.title
        ]
        independent_ok = [
            item
            for item in critical
            if len(set(item.independence_groups))
            >= policy.minimum_independent_sources_for_critical_claim
        ]
        numeric_ok = [
            item
            for item in quantitative_registry
            if item.get("status") == "traceable"
        ]
        blockers = [
            (
                f"{item.claim_id}: {item.final_status} - "
                f"{item.unresolved_issue}"
            )
            for item in critical
            if item.final_status
            not in {"verified", "verified_with_qualification"}
        ]
        if orphan:
            blockers.append(
                f"Orphan citation sources: {', '.join(orphan)}"
            )
        if unused:
            blockers.append(
                f"Unused bibliography sources: {', '.join(unused)}"
            )
        qa = EvidenceQAReport(
            policy_version=policy.policy_version,
            tool_versions={
                "full_text_retrieval": "eurskem-page-aware-v1",
                "claim_verifier": "valsci-style-exact-quote-v1",
                "citation_auditor": "eurskem-deterministic-v1",
            },
            claims_examined=len(verified_claims),
            critical_claims=len(critical),
            verified_claims=len(resolved),
            qualified_claims=len(qualified),
            mixed_or_contradicted_claims=len(mixed),
            unresolved_claims=len(verified_claims) - len(resolved),
            sources_retained=len(citation_registry),
            sources_rejected=len(rejected),
            full_text_evidence_rate=(
                1.0 if citation_registry else 0.0
            ),
            exact_locator_rate=len(exact) / max(1, len(links)),
            canonical_metadata_rate=(
                len(metadata_ok) / max(1, len(citation_registry))
            ),
            critical_contradiction_search_rate=(
                len(
                    {
                        item.claim_id for item in critical
                    }.intersection(contradiction_claims)
                )
                / max(1, len(critical))
            ),
            critical_independent_support_rate=(
                len(independent_ok) / max(1, len(critical))
            ),
            numeric_traceability_rate=(
                len(numeric_ok) / max(1, len(quantitative_registry))
                if quantitative_registry
                else 1.0
            ),
            orphan_citations=orphan,
            unused_sources=unused,
            warnings=(
                ["No proposal-grade citations passed all hard gates."]
                if not citation_registry
                else []
            ),
        )
        return qa, blockers
