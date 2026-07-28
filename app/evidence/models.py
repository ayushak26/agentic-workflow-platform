"""Typed contracts for the EU proposal evidence factory."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EvidencePurpose = Literal[
    "discovery",
    "contradiction",
    "metadata",
    "official_source",
]
EvidenceAccess = Literal[
    "metadata_only",
    "abstract_only",
    "full_text",
    "approved_internal",
]
FinalClaimStatus = Literal[
    "verified",
    "verified_with_qualification",
    "partial",
    "mixed",
    "contradicted",
    "insufficient",
    "not_found",
    "internal_verified",
    "project_commitment",
    "human_exception",
]
PairStance = Literal[
    "supports_directly",
    "supports_with_conditions",
    "mixed",
    "contradicts",
    "context_only",
    "insufficient",
    "not_relevant",
    "cannot_assess",
]


class EvidencePolicy(BaseModel):
    """Hard evidence rules applied before drafting may use a citation."""

    model_config = ConfigDict(extra="forbid")

    policy_version: str = "eurskem-evidence-v2.0"
    minimum_independent_sources_for_critical_claim: int = Field(
        default=2,
        ge=1,
        le=5,
    )
    minimum_full_text_sources_for_state_of_art_claim: int = Field(
        default=1,
        ge=1,
        le=5,
    )
    minimum_support_confidence: float = Field(default=0.72, ge=0.0, le=1.0)
    allow_abstract_only_support: bool = False
    allow_preprint_as_sole_support: bool = False
    allow_search_snippet_as_evidence: bool = False
    require_exact_locator: bool = True
    require_contradiction_search: bool = True
    require_retraction_and_correction_check: bool = True
    max_candidates_per_claim: int = Field(default=8, ge=1, le=30)
    max_full_text_documents_per_claim: int = Field(default=4, ge=1, le=10)
    max_download_bytes: int = Field(
        default=40 * 1024 * 1024,
        ge=1024,
        le=200 * 1024 * 1024,
    )


class SearchAuditRecord(BaseModel):
    claim_id: str
    query: str
    source_or_database: str
    filters: dict[str, Any] = Field(default_factory=dict)
    searched_at: str
    result_count: int = 0
    purpose: EvidencePurpose = "discovery"
    error: str | None = None


class CandidateSource(BaseModel):
    """A discovered record. It is explicitly not verified evidence."""

    candidate_id: str
    claim_id: str
    query: str
    purpose: EvidencePurpose = "discovery"
    source: str
    paper_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    canonical_url: str | None = None
    pdf_url: str | None = None
    abstract: str | None = None
    authority: str = "unverified"
    independence_group: str
    metadata_status: Literal[
        "candidate",
        "canonical",
        "conflict",
        "unresolved",
    ] = "candidate"
    retraction_status: Literal[
        "unchecked",
        "clear",
        "corrected",
        "retracted",
        "unknown",
    ] = "unchecked"
    evidence_access: EvidenceAccess = "metadata_only"
    retrieved_at: str


class FullTextDocument(BaseModel):
    """Immutable, fetched source version available for passage retrieval."""

    document_id: str
    candidate_id: str
    claim_id: str
    title: str
    citation: str
    identifier: str | None = None
    canonical_url: str | None = None
    source_type: str
    authority: str
    independence_group: str
    version_id: str
    content_sha256: str
    pdf_object_key: str
    pages_object_key: str
    page_count: int = Field(ge=1)
    evidence_access: Literal["full_text"] = "full_text"
    canonical_metadata_validated: bool = False
    retraction_status: Literal[
        "unchecked",
        "clear",
        "corrected",
        "retracted",
        "unknown",
    ] = "unchecked"
    fetched_at: str


class RetrievedPassage(BaseModel):
    passage_id: str
    document_id: str
    page: int = Field(ge=1)
    section: str | None = None
    text: str
    context: str = ""
    retrieval_score: float = Field(ge=0.0)
    passage_sha256: str


class ClaimEvidenceLink(BaseModel):
    link_id: str
    claim_id: str
    document_id: str
    candidate_id: str
    source_id: str
    passage_id: str
    exact_passage: str
    locator: dict[str, Any]
    stance: PairStance
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_score: int = Field(ge=0, le=100)
    strength: Literal["strong", "moderate", "limited", "weak", "unusable"]
    qualification: str = ""
    limitation: str = ""
    reason: str
    verifier_model: str
    verified_at: str


class CitationRegistryEntry(BaseModel):
    citation_id: str
    source_id: str
    document_id: str
    display_number: int = Field(ge=1)
    title: str
    authors: list[str] = Field(default_factory=list)
    organisation: str | None = None
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    canonical_url: str | None = None
    identifier: str | None = None
    source_type: str
    version_id: str
    content_sha256: str
    retraction_status: str
    formatted_citation: str
    claim_ids: list[str] = Field(default_factory=list)


class VerifiedClaim(BaseModel):
    claim_id: str
    original_text: str
    atomic_claim: str
    claim_type: str
    section_id: str = ""
    materiality: Literal["critical", "important", "contextual"]
    evidence_requirement: str
    final_status: FinalClaimStatus
    verified_sentence: str
    inline_citation_tokens: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    evidence_link_ids: list[str] = Field(default_factory=list)
    independence_groups: list[str] = Field(default_factory=list)
    qualification: str = ""
    unresolved_issue: str = ""
    human_review_required: bool = False


class RejectedCandidate(BaseModel):
    claim_id: str
    candidate_title: str
    candidate_identifier: str | None = None
    reason: str
    notes: str = ""


class EvidenceGap(BaseModel):
    claim_id: str
    gap: str
    proposal_risk: str
    recommended_action: Literal[
        "search_again",
        "narrow_claim",
        "replace_claim",
        "obtain_internal_evidence",
        "human_review",
        "delete_claim",
    ]
    owner: str = ""
    blocking: bool = False


class EvidenceQAReport(BaseModel):
    policy_version: str
    tool_versions: dict[str, str] = Field(default_factory=dict)
    claims_examined: int = 0
    critical_claims: int = 0
    verified_claims: int = 0
    qualified_claims: int = 0
    mixed_or_contradicted_claims: int = 0
    unresolved_claims: int = 0
    sources_retained: int = 0
    sources_rejected: int = 0
    full_text_evidence_rate: float = 0.0
    exact_locator_rate: float = 0.0
    canonical_metadata_rate: float = 0.0
    critical_contradiction_search_rate: float = 0.0
    critical_independent_support_rate: float = 0.0
    numeric_traceability_rate: float = 0.0
    orphan_citations: list[str] = Field(default_factory=list)
    unused_sources: list[str] = Field(default_factory=list)
    citation_drift_findings: list[str] = Field(default_factory=list)
    metadata_conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProposalEvidencePackage(BaseModel):
    verified_claims: list[VerifiedClaim] = Field(default_factory=list)
    citation_registry: list[CitationRegistryEntry] = Field(default_factory=list)
    claim_evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)
    quantitative_evidence_registry: list[dict[str, Any]] = Field(
        default_factory=list
    )
    proposal_ready_cited_markdown: str = ""
    bibliography_markdown: str = ""
    bibliography_bibtex: str = ""
    search_audit: list[SearchAuditRecord] = Field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    qa_report: EvidenceQAReport
    blocking_issues: list[str] = Field(default_factory=list)
