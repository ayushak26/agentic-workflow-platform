"""CitationRegistryBuilder: deterministic reshaping of acquired full-text
documents into a renderer-shaped, numbered citation registry.

This node exists because the Horizon DOCX renderer's footnote and bibliography
engines (``app.tools.docx_proposal_rendering``) read three specific keys off
each citation dict, in this priority order:

    footnote/bib text  = citation["formatted_citation"]
                         or citation["title"]
                         or citation["citation_id"]
    marker matching    = citation["display_number"]  (matched against ``[N]``)
    bookmark anchor    = citation["citation_id"]

The acquired-evidence artifact (``candidates_processed.documents``) does NOT
carry those keys - it carries ``citation``, ``canonical_url``, ``document_id``,
``claim_id``, ``title``. Passing it straight to the renderer makes every
footnote fall through to the bare ``title`` with no URL and no number that
matches the drafters' ``[N]`` markers, so footnotes silently do not render.

This node performs a pure, deterministic transform (no LLM, no network):

  * de-duplicates physically identical sources (same ``content_sha256`` /
    ``canonical_url``) so one real source maps to exactly one ``[N]`` - the
    supplied documents list repeats the same paper under many ``document_id``
    values, one per claim it supports;
  * assigns a stable, sequential ``display_number`` in first-appearance order;
  * folds ``canonical_url`` INTO ``formatted_citation`` (the only place the
    renderer will surface it in a footnote);
  * emits a ``claim_to_numbers`` map and a compact ``drafting_guide`` so the
    upstream drafters can cite each claim with the correct ``[N]`` integer,
    which is what makes the footnotes actually fire.

Because citation integrity is the whole point of the system, the reshape is
deterministic and never delegated to a model: no row is dropped, renumbered,
or invented.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


class CitationRegistryBuilderInput(BaseModel):
    """Pydantic model defining the CitationRegistryBuilderInput shape."""
    pass


class CitationRegistryBuilderConfig(BaseModel):
    # ``documents`` is templated from ``inputs.candidates_processed.documents``.
    # Accept the whole ``candidates_processed`` object too, for convenience.
    """Pydantic model defining the CitationRegistryBuilderConfig shape.

    Attributes:
        documents (str | list[dict[str, Any]]).
        require_full_text (bool).
    """
    documents: str | list[dict[str, Any]] = Field(default_factory=list)
    # When True, only documents whose evidence_access == "full_text" become
    # citable footnotes. Acquired-but-partial sources are excluded rather than
    # cited as if fully verified.
    require_full_text: bool = True

    @field_validator("documents", mode="before")
    @classmethod
    def _coerce_documents(cls, value: Any) -> Any:
        # Allow either the raw documents list, or the full candidates_processed
        # object ({"documents": [...]}) so the YAML can wire either one.
        """Internal helper for the coerce documents step.

        Args:
            value (Any): Value to process.

        Returns:
            Any: The documents.
        """
        if isinstance(value, dict) and "documents" in value:
            return value["documents"]
        return value


class CitationEntry(BaseModel):
    """Pydantic model defining the CitationEntry shape.

    Attributes:
        display_number (int).
        citation_id (str).
        title (str).
        formatted_citation (str).
        canonical_url (str).
        identifier (str).
        source_type (str).
        authority (str).
    """
    display_number: int
    citation_id: str
    title: str
    formatted_citation: str
    canonical_url: str
    identifier: str
    source_type: str
    authority: str
    claim_ids: list[str] = Field(default_factory=list)


class CitationRegistryBuilderOutput(BaseModel):
    # ``citation_registry`` is the list the renderer consumes directly.
    """Pydantic model defining the CitationRegistryBuilderOutput shape.

    Attributes:
        citation_registry (list[CitationEntry]).
        claim_to_numbers (dict[str, list[int]]).
        drafting_guide (str).
        total_sources (int).
        total_documents_considered (int).
        excluded_documents (int).
    """
    citation_registry: list[CitationEntry] = Field(default_factory=list)
    # ``claim_to_numbers`` maps each claim id -> the [N] display numbers that
    # support it, so drafters emit the correct integer markers.
    claim_to_numbers: dict[str, list[int]] = Field(default_factory=dict)
    # ``drafting_guide`` is a compact, human/LLM-readable rendering of the
    # numbering, handed to the drafters so their ``[N]`` markers align.
    drafting_guide: str = ""
    total_sources: int = 0
    total_documents_considered: int = 0
    excluded_documents: int = 0


@NodeRegistry.register
class CitationRegistryBuilder(NodeType):
    """Workflow node type implementing the CitationRegistryBuilder capability.

    Attributes:
        input_schema (ClassVar[type[BaseModel]]).
        config_schema (ClassVar[type[BaseModel]]).
        output_schema (ClassVar[type[BaseModel]]).
    """
    type_name = "CitationRegistryBuilder"
    description = (
        "Deterministically reshape acquired full-text documents into a "
        "numbered, renderer-ready citation registry with canonical URLs "
        "folded into each formatted citation."
    )
    input_schema: ClassVar[type[BaseModel]] = CitationRegistryBuilderInput
    config_schema: ClassVar[type[BaseModel]] = CitationRegistryBuilderConfig
    output_schema: ClassVar[type[BaseModel]] = CitationRegistryBuilderOutput

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
        cfg = CitationRegistryBuilderConfig(**resolved_config)
        if isinstance(cfg.documents, str):
            raise ValueError(
                "documents template did not resolve to a list; expected "
                "inputs.candidates_processed.documents"
            )

        documents: list[dict[str, Any]] = list(cfg.documents)
        considered = len(documents)
        excluded = 0

        # Group physically identical sources. A source's identity is its
        # content hash when present, else its canonical URL, else its
        # identifier. This collapses the many-document_id-per-paper duplication
        # in the acquired-evidence artifact down to one [N] per real source.
        order: list[str] = []
        by_source: dict[str, dict[str, Any]] = {}
        claim_ids_by_source: dict[str, list[str]] = {}

        for doc in documents:
            if cfg.require_full_text and doc.get("evidence_access") not in (
                None,
                "full_text",
            ):
                excluded += 1
                continue

            key = str(
                doc.get("content_sha256")
                or doc.get("canonical_url")
                or doc.get("identifier")
                or doc.get("document_id")
                or ""
            ).strip()
            if not key:
                excluded += 1
                continue

            if key not in by_source:
                by_source[key] = doc
                claim_ids_by_source[key] = []
                order.append(key)

            claim_id = str(doc.get("claim_id") or "").strip()
            if claim_id and claim_id not in claim_ids_by_source[key]:
                claim_ids_by_source[key].append(claim_id)

        registry: list[CitationEntry] = []
        claim_to_numbers: dict[str, list[int]] = {}

        for display_number, key in enumerate(order, start=1):
            doc = by_source[key]
            claim_ids = claim_ids_by_source[key]

            citation_text = str(doc.get("citation") or "").strip()
            title = str(doc.get("title") or "").strip() or "Untitled source"
            canonical_url = str(doc.get("canonical_url") or "").strip()

            # Fold the canonical URL into the formatted citation - this is the
            # ONLY place the renderer will surface a URL in a footnote, because
            # _footnote_body_text reads formatted_citation and nothing else for
            # the link. Avoid double-printing the URL if the formatted citation
            # already ends with it.
            base = citation_text or title
            if canonical_url and canonical_url not in base:
                formatted = f"{base} {canonical_url}".strip()
            else:
                formatted = base

            # Use the stable content-addressed source id for the bookmark
            # anchor where available, else the document_id.
            citation_id = str(
                doc.get("version_id")
                or doc.get("document_id")
                or f"CIT-{display_number:04d}"
            )

            registry.append(
                CitationEntry(
                    display_number=display_number,
                    citation_id=citation_id,
                    title=title,
                    formatted_citation=formatted,
                    canonical_url=canonical_url,
                    identifier=str(doc.get("identifier") or ""),
                    source_type=str(doc.get("source_type") or ""),
                    authority=str(doc.get("authority") or ""),
                    claim_ids=claim_ids,
                )
            )

            for claim_id in claim_ids:
                claim_to_numbers.setdefault(claim_id, []).append(
                    display_number
                )

        # Build a compact drafting guide: claim -> which [N] markers to use,
        # plus the source title so the drafter cites the right one in context.
        guide_lines: list[str] = [
            "Cite sources using bracketed integer markers like [1], [2] that "
            "match the display numbers below. Only these numbers are "
            "footnoted; any other citation form (e.g. [CL-5], author-year) "
            "will NOT render as a footnote.",
            "",
        ]
        if claim_to_numbers:
            guide_lines.append("Claim -> supporting citation numbers:")
            for claim_id in sorted(claim_to_numbers):
                numbers = ", ".join(
                    f"[{n}]" for n in claim_to_numbers[claim_id]
                )
                guide_lines.append(f"  {claim_id}: {numbers}")
            guide_lines.append("")
        guide_lines.append("Citation list:")
        for entry in registry:
            claims = (
                f" (supports {', '.join(entry.claim_ids)})"
                if entry.claim_ids
                else ""
            )
            guide_lines.append(
                f"  [{entry.display_number}] {entry.title}{claims}"
            )
        drafting_guide = "\n".join(guide_lines)

        return CitationRegistryBuilderOutput(
            citation_registry=registry,
            claim_to_numbers=claim_to_numbers,
            drafting_guide=drafting_guide,
            total_sources=len(registry),
            total_documents_considered=considered,
            excluded_documents=excluded,
        ).model_dump(mode="json")