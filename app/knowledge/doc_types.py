"""The document types a Collection can declare.

A Collection's ``doc_types`` is what kind of material it holds. It is an open
vocabulary — a custom value is always allowed — but two things read it, so the
common values are worth offering explicitly rather than leaving to free text:

1. **Auto embedding-model selection.** Types flagged ``precision_sensitive``
   make ``select_embedding_model`` choose the higher-quality model, because
   retrieving the wrong clause of a contract or the wrong tolerance from a spec
   is expensive in a way a wrong paragraph of general prose is not.
2. **Retrieval filtering.** ``doc_type`` is a standard metadata field, so a
   retrieval filter or a workflow's ``runtime_filters`` can narrow a search to
   one kind of document within a Collection.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.ingestion.embedding_catalog import PRECISION_SENSITIVE_DOC_TYPES


@dataclass(frozen=True)
class DocTypeChoice:
    id: str
    label: str
    description: str

    @property
    def precision_sensitive(self) -> bool:
        return self.id in PRECISION_SENSITIVE_DOC_TYPES


DOC_TYPES: tuple[DocTypeChoice, ...] = (
    DocTypeChoice("general", "General", "Mixed prose with no special handling."),
    DocTypeChoice(
        "technical_documentation",
        "Technical documentation",
        "Product manuals, datasheets, engineering documents.",
    ),
    DocTypeChoice("manual", "Manual", "Operating, service or installation manuals."),
    DocTypeChoice("spec", "Specification", "Specifications, tolerances, part definitions."),
    DocTypeChoice("policy", "Policy", "Internal policies and standard operating procedures."),
    DocTypeChoice("contract", "Contract", "Contracts, terms, commercial agreements."),
    DocTypeChoice("legal", "Legal", "Legal opinions, filings, case material."),
    DocTypeChoice("regulation", "Regulation", "Regulatory texts and directives."),
    DocTypeChoice("standard", "Standard", "Published standards such as ISO or EN."),
    DocTypeChoice("research", "Research", "Papers, studies, scientific reports."),
    DocTypeChoice("report", "Report", "Business and project reports."),
    DocTypeChoice("presentation", "Presentation", "Slide decks and briefing material."),
    DocTypeChoice("email", "Email", "Correspondence and message threads."),
    DocTypeChoice("faq", "FAQ / knowledge base", "Short question-and-answer material."),
    DocTypeChoice("meeting_notes", "Meeting notes", "Minutes, notes, action lists."),
    DocTypeChoice("financial", "Financial", "Invoices, quotations, financial statements."),
)

DOC_TYPES_BY_ID = {choice.id: choice for choice in DOC_TYPES}


def doc_type_catalog() -> list[dict[str, object]]:
    return [
        {**asdict(choice), "precision_sensitive": choice.precision_sensitive}
        for choice in DOC_TYPES
    ]
