"""Proposal-grade evidence gathering and citation verification.

The package deliberately separates discovery from verification:

candidate metadata -> fetched source version -> exact passage -> verified link

Search results and abstracts are useful for finding documents, but they are
never treated as evidence for a proposal claim.
"""

from .models import (
    CandidateSource,
    CitationRegistryEntry,
    EvidenceGap,
    EvidencePolicy,
    FullTextDocument,
    ProposalEvidencePackage,
    SearchAuditRecord,
)

__all__ = [
    "CandidateSource",
    "CitationRegistryEntry",
    "EvidenceGap",
    "EvidencePolicy",
    "FullTextDocument",
    "ProposalEvidencePackage",
    "SearchAuditRecord",
]
