"""Canonical scholarly identifier extraction and work identity.

Why this exists: the same paper legitimately arrives from different lanes and
backends under different URLs — a publisher landing page, a doi.org
redirect, an arXiv abs page, a PMC record, an OpenAlex work URL — and
previously only a bare ``doi.org`` hostname was recognised as carrying a DOI
(see the old ``_doi_from_url`` in app/nodes/bounded_deep_research_agent.py).
Everything else fell through to URL- or title-based identity, so the same
work survived as several "distinct" candidates across lanes, and genuinely
peer-reviewed sources were misclassified as unverified web pages.

Two responsibilities, deliberately separated:

1. ``extract_identifiers`` — offline, deterministic, no network. Pulls
   DOI/PMID/PMCID/arXiv/OpenAlex/Semantic-Scholar ids out of a URL (and
   optionally out of already-fetched metadata/HTML). This is what dedup and
   authority classification run on today.
2. ``work_identity`` — the single cross-lane dedup key derived from those
   identifiers, falling back to a normalised title only when no strong
   identifier exists.

Network-backed resolution (Crossref title lookup, OpenAlex, Semantic
Scholar) is deliberately NOT here yet — it belongs with the citation-graph
client work, where the same HTTP clients are needed anyway. ``ResolvedWork``
already carries the fields that resolution will populate so adding it later
does not change this module's public shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

# A DOI is "10." + registrant + "/" + suffix. Suffix charset per the DOI
# handbook is deliberately permissive; we stop at whitespace, quotes, angle
# brackets, and common trailing punctuation that comes from surrounding
# prose/markup rather than from the DOI itself.
_DOI_CORE = r"10\.\d{4,9}/[^\s\"'<>&]+"
_DOI_ANYWHERE = re.compile(_DOI_CORE, re.IGNORECASE)
_DOI_TRAILING_JUNK = re.compile(r"[.,;:)\]}>]+$")

_ARXIV_NEW = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")
_ARXIV_OLD = re.compile(r"\b([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")
_PMCID = re.compile(r"\bPMC(\d{6,9})\b", re.IGNORECASE)
_OPENALEX = re.compile(r"\b(W\d{6,12})\b")

_TITLE_NOISE = re.compile(r"[^a-z0-9]+")


@dataclass
class ResolvedWork:
    """Canonical identity for one scholarly work, as far as it is known.

    Every field is optional: a grey-literature web page legitimately has
    none of them, and that is a valid state (``work_identity`` then falls
    back to the normalised title). Fields populated only by future
    network-backed resolution are grouped at the end.
    """

    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    # Populated by network resolution (not implemented in this phase).
    publication_type: str | None = None
    publication_status: str | None = None
    venue: str | None = None
    publisher: str | None = None
    is_preprint: bool | None = None
    is_retracted: bool | None = None
    resolution_sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, str]:
        """Only the populated identifier fields, for storage on a candidate."""
        out: dict[str, str] = {}
        for key in (
            "doi",
            "pmid",
            "pmcid",
            "arxiv_id",
            "openalex_id",
            "semantic_scholar_id",
        ):
            value = getattr(self, key)
            if value:
                out[key] = value
        return out

    @property
    def has_strong_identifier(self) -> bool:
        """The has strong identifier."""
        return bool(
            self.doi
            or self.pmid
            or self.pmcid
            or self.arxiv_id
            or self.openalex_id
            or self.semantic_scholar_id
        )

    def is_peer_reviewed(self) -> bool:
        """True ONLY when resolved metadata positively supports it.

        Returns False for "unknown" — the caller then reports
        ``scholarly_status_unconfirmed`` rather than guessing. Since
        network-backed resolution isn't implemented yet, this is False for
        everything today; it exists so classify_authority's contract is
        already correct and gains accuracy the moment resolution lands,
        with no call-site changes.
        """
        if self.is_retracted or self.is_preprint:
            return False
        if self.publication_status and self.publication_status.lower() in {
            "retracted",
            "withdrawn",
            "preprint",
            "submitted",
        }:
            return False
        peer_reviewed_types = {
            "journal-article",
            "journal_article",
            "proceedings-article",
            "proceedings_article",
            "article",
        }
        if (self.publication_type or "").lower().replace(" ", "-") in (
            peer_reviewed_types
        ):
            # A venue/publisher is what distinguishes a real journal article
            # record from a bare type label on an otherwise-unknown item.
            return bool(self.venue or self.publisher)
        return False


def normalise_doi_value(value: Any) -> str | None:
    """Lowercased bare DOI from anything that might contain one.

    Accepts a bare DOI, a ``doi:`` prefix, or any URL with the DOI in its
    path or query — which is the common publisher-landing-page case the old
    hostname-only check missed entirely.
    """
    if not value:
        return None
    text = unquote(str(value)).strip()
    match = _DOI_ANYWHERE.search(text)
    if not match:
        return None
    return _DOI_TRAILING_JUNK.sub("", match.group(0)).lower()


def _arxiv_from(text: str) -> str | None:
    """Internal helper for the arxiv from step.

    Args:
        text (str): The text.

    Returns:
        str | None: The from.
    """
    new = _ARXIV_NEW.search(text)
    if new:
        return new.group(1)
    old = _ARXIV_OLD.search(text)
    if old:
        return old.group(1)
    return None


def extract_identifiers(
    url: str | None = None,
    *,
    metadata: dict[str, Any] | None = None,
    text: str | None = None,
) -> ResolvedWork:
    """Best-effort offline identifier extraction.

    ``url`` is the candidate's landing page. ``metadata`` is any
    already-parsed record (a search-backend result dict, HTML meta tags,
    JSON-LD) whose keys are checked case-insensitively. ``text`` is free
    text (page body, PDF text) scanned only as a last resort, since prose
    can mention other works' DOIs -- so it is used ONLY for fields no
    stronger input supplied.
    """
    work = ResolvedWork()
    url = (url or "").strip()
    host = (urlsplit(url).hostname or "").lower() if url else ""
    lowered_meta: dict[str, Any] = {}
    if metadata:
        lowered_meta = {
            str(k).strip().lower().replace("-", "_").replace(".", "_"): v
            for k, v in metadata.items()
        }

    def meta(*keys: str) -> Any:
        """Compute the meta.

        Args:
            *keys (str): The keys.

        Returns:
            Any: The result.
        """
        for key in keys:
            value = lowered_meta.get(key)
            if value not in (None, "", [], {}):
                return value
        return None

    # --- DOI: explicit metadata first, then the URL, then free text.
    work.doi = (
        normalise_doi_value(meta("doi", "citation_doi", "dc_identifier"))
        or normalise_doi_value(url)
        or (normalise_doi_value(text) if text else None)
    )

    # --- arXiv: host-scoped or explicit, never from arbitrary free text
    # (a bare "2401.12345"-shaped number is far too common to trust).
    explicit_arxiv = meta("arxiv_id", "arxiv", "citation_arxiv_id")
    if explicit_arxiv:
        work.arxiv_id = _arxiv_from(str(explicit_arxiv))
    elif "arxiv.org" in host:
        work.arxiv_id = _arxiv_from(url)
    if not work.arxiv_id and work.doi and work.doi.startswith("10.48550/arxiv."):
        # arXiv's own DOI prefix carries the id.
        work.arxiv_id = work.doi.split("10.48550/arxiv.", 1)[1] or None

    # --- PubMed / PMC.
    explicit_pmid = meta("pmid", "pubmed_id", "citation_pmid")
    if explicit_pmid:
        digits = re.sub(r"\D", "", str(explicit_pmid))
        work.pmid = digits or None
    elif "pubmed.ncbi.nlm.nih.gov" in host:
        segment = urlsplit(url).path.strip("/").split("/")[0]
        work.pmid = segment if segment.isdigit() else None

    explicit_pmcid = meta("pmcid", "pmc_id")
    pmcid_match = _PMCID.search(
        str(explicit_pmcid) if explicit_pmcid else (url if "ncbi" in host else "")
    )
    if pmcid_match:
        work.pmcid = f"PMC{pmcid_match.group(1)}"

    # --- OpenAlex / Semantic Scholar.
    explicit_openalex = meta("openalex_id", "openalex")
    openalex_match = _OPENALEX.search(
        str(explicit_openalex)
        if explicit_openalex
        else (url if "openalex.org" in host else "")
    )
    if openalex_match:
        work.openalex_id = openalex_match.group(1)

    # A bare `paper_id` is only a Semantic Scholar id when the record
    # actually came from Semantic Scholar — every backend has a `paper_id`,
    # so trusting it unconditionally would mint bogus S2 ids (and thus bogus
    # cross-lane dedup collisions) from arXiv/PubMed/CORE records.
    explicit_s2 = lowered_meta.get("semantic_scholar_id")
    if explicit_s2 is None and (
        "semantic" in host
        or str(lowered_meta.get("source", "")).strip().lower() == "semantic"
    ):
        explicit_s2 = meta("paperid", "paper_id", "corpusid", "corpus_id")
    if explicit_s2:
        s2 = str(explicit_s2).strip()
        if re.fullmatch(r"[0-9a-f]{40}|\d+", s2, re.IGNORECASE):
            work.semantic_scholar_id = s2
    elif "semanticscholar.org" in host:
        segments = [s for s in urlsplit(url).path.split("/") if s]
        for segment in reversed(segments):
            if re.fullmatch(r"[0-9a-f]{40}", segment, re.IGNORECASE):
                work.semantic_scholar_id = segment
                break

    return work


def normalise_title(title: str | None) -> str:
    """Punctuation/whitespace-insensitive title key.

    Shared by dedup and by identity fallback so the two can never disagree
    about whether two titles are "the same".
    """
    return _TITLE_NOISE.sub(" ", (title or "").lower()).strip()


def work_identity(
    work: ResolvedWork | None,
    *,
    title: str | None = None,
    fallback: str | None = None,
) -> str:
    """The single cross-lane dedup key for one work.

    Strong identifiers win in a fixed precedence order so that two lanes
    that each resolved a different subset of identifiers still collide as
    long as they share ANY one of them... with one caveat worth stating:
    precedence means a candidate resolving only {arxiv_id} and another
    resolving only {doi} for the same paper will NOT collide here. Closing
    that needs cross-identifier resolution (arXiv id -> DOI), which is
    network-backed and belongs with the citation-graph work. Within one
    backend's results, identifier sets are consistent enough that this
    precedence order already collapses the common cross-lane duplicates.
    """
    if work is not None:
        if work.doi:
            return f"doi:{work.doi}"
        if work.pmcid:
            return f"pmcid:{work.pmcid.upper()}"
        if work.pmid:
            return f"pmid:{work.pmid}"
        if work.arxiv_id:
            return f"arxiv:{work.arxiv_id.lower()}"
        if work.openalex_id:
            return f"openalex:{work.openalex_id.upper()}"
        if work.semantic_scholar_id:
            return f"s2:{work.semantic_scholar_id.lower()}"
    normalised = normalise_title(title)
    if normalised:
        return f"title:{normalised}"
    return f"opaque:{fallback or ''}"


_PREPRINT_HOSTS = ("arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com")
_OFFICIAL_EU_HOSTS = ("europa.eu",)


def classify_authority(
    url: str | None,
    work: ResolvedWork,
) -> tuple[str, str]:
    """``(source_label, authority)`` from what is actually known.

    Deliberately does NOT treat "has a DOI" as "peer reviewed": a DOI is
    also minted for datasets, preprints, editorials, corrections,
    conference abstracts, protocols, book chapters, and retracted items.
    Until resolved publication metadata says otherwise (a later phase),
    a DOI-bearing source whose venue/type is unknown is reported as
    ``scholarly_status_unconfirmed`` rather than being promoted to
    peer-reviewed on no evidence.
    """
    host = (urlsplit(url or "").hostname or "").lower()

    if host.endswith(_OFFICIAL_EU_HOSTS):
        return "official_eu", "official_eu"

    if work.is_retracted:
        return ("doi" if work.doi else "web"), "retracted"

    if work.is_preprint or any(h in host for h in _PREPRINT_HOSTS):
        return "preprint", "preprint"

    if work.is_peer_reviewed():
        return ("doi" if work.doi else "scholarly"), "peer_reviewed"

    if work.has_strong_identifier:
        return ("doi" if work.doi else "scholarly"), "scholarly_status_unconfirmed"

    return "web", "unverified"
