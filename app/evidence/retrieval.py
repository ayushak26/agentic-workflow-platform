"""Deterministic helpers for discovery, full-text handling, and passages."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.evidence.identifiers import (
    ResolvedWork,
    extract_identifiers,
    work_identity,
)
from app.evidence.models import CandidateSource, RetrievedPassage

# The identifier-carrying fields of ResolvedWork — the only keys accepted
# out of a candidate's stored canonical_identifiers dict, so a stray key
# can never blow up ResolvedWork(**...) construction during dedup.
_RESOLVED_WORK_ID_FIELDS = frozenset(
    {
        "doi",
        "pmid",
        "pmcid",
        "arxiv_id",
        "openalex_id",
        "semantic_scholar_id",
    }
)


_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*")
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "into", "is", "it", "of", "on", "or", "that",
    "the", "their", "this", "to", "was", "were", "will", "with",
}


def utc_now() -> str:
    """Compute the utc now.

    Returns:
        str: The now.
    """
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: Any, length: int = 16) -> str:
    """Compute the stable id.

    Args:
        prefix (str): Prefix string.
        *parts (Any): Path segments.
        length (int): The length (optional, default 16).

    Returns:
        str: The id.
    """
    basis = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}-{digest}"


def normalise_doi(value: Any) -> str | None:
    """Compute the normalise doi.

    Args:
        value (Any): Value to process.

    Returns:
        str | None: The doi.
    """
    if not value:
        return None
    doi = str(value).strip()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    return doi.lower() if _DOI_RE.fullmatch(doi) else None


def parse_authors(value: Any) -> list[str]:
    """Parse the authors.

    Args:
        value (Any): Value to process.

    Returns:
        list[str]: The authors.
    """
    if isinstance(value, str):
        splitter = ";" if ";" in value else ","
        return [item.strip() for item in value.split(splitter) if item.strip()]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or " ".join(
                    part for part in (item.get("given"), item.get("family")) if part
                )
            else:
                name = str(item)
            if name.strip():
                result.append(name.strip())
        return result
    return []


def parse_year(value: Any) -> int | None:
    """Parse the year.

    Args:
        value (Any): Value to process.

    Returns:
        int | None: The year.
    """
    match = re.search(r"\b(19|20)\d{2}\b", str(value or ""))
    return int(match.group(0)) if match else None


def first_value(data: dict[str, Any], *keys: str) -> Any:
    """Compute the first value.

    Args:
        data (dict[str, Any]): Data mapping.
        *keys (str): The keys.

    Returns:
        Any: The value.
    """
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def parse_mcp_payload(raw: Any) -> Any:
    """Parse the mcp payload.

    Args:
        raw (Any): Raw value.

    Returns:
        Any: The mcp payload.
    """
    if not isinstance(raw, str):
        return raw
    value: Any = raw.strip()
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            break
    return value


def papers_from_payload(raw: Any) -> list[dict[str, Any]]:
    """Compute the papers from payload.

    Args:
        raw (Any): Raw value.

    Returns:
        list[dict[str, Any]]: The from payload.
    """
    payload = parse_mcp_payload(raw)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("papers", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if payload.get("title"):
            return [payload]
    return []


def source_errors_from_payload(raw: Any) -> dict[str, str]:
    """Per-source failures from a multi-source search payload (e.g.
    paper-search-mcp's ``search_papers``), which reports an overall success
    with an ``errors: {source: message}`` map for individual sources that
    failed internally (auth rejection, rate limit, provider outage) — see
    SearchAuditRecord.source_errors for why this matters. Returns {} for any
    payload shape without an ``errors`` dict (single-source tools, or a tool
    that doesn't report per-source detail at all)."""
    payload = parse_mcp_payload(raw)
    if not isinstance(payload, dict):
        return {}
    errors = payload.get("errors")
    if not isinstance(errors, dict):
        return {}
    return {str(k): str(v) for k, v in errors.items()}


def candidate_from_paper(
    paper: dict[str, Any],
    *,
    claim_id: str,
    query: str,
    purpose: str,
    source_hint: str | None = None,
    discovery_lane: str | None = None,
) -> CandidateSource:
    """Compute the candidate from paper.

    Args:
        paper (dict[str, Any]): The paper.
        claim_id (str): The claim id.
        query (str): Query filter.
        purpose (str): The purpose.
        source_hint (str | None): The source hint (optional, default None).
        discovery_lane (str | None): The discovery lane (optional, default None).

    Returns:
        CandidateSource: The from paper.
    """
    source = str(
        first_value(paper, "source", "platform") or source_hint or "unknown"
    ).strip().lower()
    title = str(first_value(paper, "title") or "(title unavailable)").strip()
    paper_id_value = first_value(paper, "paper_id", "id", "pmid")
    paper_id = str(paper_id_value).strip() if paper_id_value else None
    doi = normalise_doi(first_value(paper, "doi", "DOI"))
    canonical_url = first_value(
        paper,
        "url",
        "canonical_url",
        "landing_page_url",
        "link",
    )
    pdf_url = first_value(paper, "pdf_url", "openaccess_url", "open_access_url")
    authors = parse_authors(first_value(paper, "authors", "author"))
    year = parse_year(
        first_value(
            paper,
            "published_date",
            "publication_date",
            "published",
            "year",
        )
    )
    identifier = doi or paper_id or canonical_url or title
    independence_group = stable_id("IG", doi or title.lower(), length=12)
    retracted_value = first_value(
        paper,
        "is_retracted",
        "retracted",
        "retraction_status",
    )
    if isinstance(retracted_value, bool):
        retraction_status = "retracted" if retracted_value else "clear"
    elif str(retracted_value or "").strip().lower() in {
        "retracted",
        "true",
        "yes",
    }:
        retraction_status = "retracted"
    elif str(retracted_value or "").strip().lower() in {
        "clear",
        "false",
        "no",
        "not_retracted",
    }:
        retraction_status = "clear"
    else:
        retraction_status = "unchecked"
    authority = {
        "arxiv": "preprint",
        "biorxiv": "preprint",
        "medrxiv": "preprint",
        "europepmc": "peer_reviewed",
        "pmc": "peer_reviewed",
        "pubmed": "peer_reviewed",
        "crossref": "peer_reviewed",
        "openalex": "peer_reviewed",
        "doaj": "peer_reviewed",
        "openaire": "official_eu",
        "core": "grey",
        "zenodo": "grey",
        "hal": "grey",
    }.get(source, "unverified")
    return CandidateSource(
        candidate_id=stable_id(
            "CAND",
            claim_id,
            identifier,
            purpose,
        ),
        claim_id=claim_id,
        query=query,
        purpose=purpose,
        source=source,
        paper_id=paper_id,
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        canonical_url=str(canonical_url) if canonical_url else None,
        pdf_url=str(pdf_url) if pdf_url else None,
        abstract=(
            str(first_value(paper, "abstract", "summary", "snippet"))
            if first_value(paper, "abstract", "summary", "snippet")
            else None
        ),
        authority=authority,
        independence_group=independence_group,
        # Resolve identifiers from the backend record AND the landing-page
        # URL, so this lane's candidates carry the same canonical identity
        # keys the deep-research lane produces — that shared identity is
        # what lets deduplicate_candidates collapse the same work found by
        # both lanes instead of acquiring it twice.
        canonical_identifiers=extract_identifiers(
            str(canonical_url) if canonical_url else None,
            metadata=paper,
        ).as_dict(),
        discovery_lane=discovery_lane,
        metadata_status="canonical" if doi and title != "(title unavailable)" else "candidate",
        retraction_status=retraction_status,
        retrieved_at=utc_now(),
    )


def _normalise_title_for_identity(title: str) -> str:
    """Collapse cosmetic title differences that otherwise defeat dedup.

    The same paper routinely comes back from different search backends
    (arxiv/openalex/europepmc/core/...) with cosmetically different title
    strings — a trailing period, curly vs straight quotes, doubled
    whitespace, or a subtitle one source includes and another drops. A raw
    ``.lower()`` fallback treats each of those as a distinct paper.
    """

    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def candidate_work_identity(candidate: CandidateSource) -> str:
    """Cross-lane work identity for one candidate.

    Prefers the canonical identifiers resolved by
    app/evidence/identifiers.py (shared with the deep-research lane, so the
    same paper found via a publisher landing page, a doi.org link, or an
    arXiv abs page collapses to one identity). Falls back to this module's
    historical URL/title identity only when no strong identifier exists —
    which is the correct behaviour for grey literature and web pages that
    genuinely have no scholarly identifier.
    """
    work = ResolvedWork(**{
        key: value
        for key, value in (candidate.canonical_identifiers or {}).items()
        if key in _RESOLVED_WORK_ID_FIELDS
    })
    if not work.has_strong_identifier:
        # Fill from the flat legacy fields so candidates created before
        # canonical_identifiers existed still dedupe on their DOI.
        if candidate.doi:
            work.doi = candidate.doi
    if work.has_strong_identifier:
        return work_identity(work)
    if candidate.paper_id:
        return f"paper:{candidate.source}:{candidate.paper_id}"
    if candidate.canonical_url:
        return f"url:{candidate.source}:{candidate.canonical_url}"
    return f"title:{_normalise_title_for_identity(candidate.title)}"


def deduplicate_candidates(
    candidates: list[CandidateSource],
) -> list[CandidateSource]:
    """Deduplicate the same work for one claim, across queries AND lanes.

    Identity is the canonical work identity (see
    ``candidate_work_identity``): resolved scholarly identifiers first, then
    paper_id, then the same canonical_url from the same backend, then a
    punctuation/whitespace-normalised title. This is what makes the function
    safe to run on a FUSED list from several research lanes — the same paper
    found by both the scholarly-search lane and the deep-research lane now
    collapses to one candidate instead of being acquired twice.

    purpose is deliberately NOT part of the identity key: fanning discovery
    and contradiction queries out to the same set of backends means the
    same paper commonly surfaces from both, and previously survived as two
    "different" candidates. When a discovery-tagged and a contradiction-
    tagged hit collide, the contradiction tag is kept — that signal is
    scarce and specifically capped downstream, so it must not be silently
    absorbed by an earlier discovery-purpose duplicate.

    When duplicates come from DIFFERENT lanes, the surviving candidate keeps
    a record of every lane that found it (``discovery_lane`` becomes a
    comma-joined list) so cross-lane overlap stays measurable after dedup
    rather than being silently discarded. The richer record wins on merge:
    a candidate carrying canonical identifiers beats one that resolved none.
    """

    kept: dict[tuple[str, str], CandidateSource] = {}
    order: list[tuple[str, str]] = []
    lanes: dict[tuple[str, str], list[str]] = {}
    # Per claim, the identity key already claimed by a given normalised
    # title. This closes the remaining cross-lane gap that identifier
    # precedence alone cannot: one lane resolves a DOI for a paper while
    # another lane only has a landing-page URL, so the two produce
    # DIFFERENT strong/weak identity keys despite being the same work.
    # Within a single claim, an identical non-placeholder title is a strong
    # enough signal to treat them as one work.
    title_alias: dict[tuple[str, str], tuple[str, str]] = {}

    def _lane_list(key: tuple[str, str]) -> list[str]:
        """Internal helper for the lane list step.

        Args:
            key (tuple[str, str]): Lookup key.

        Returns:
            list[str]: The list.
        """
        return lanes.setdefault(key, [])

    for candidate in candidates:
        key = (candidate.claim_id, candidate_work_identity(candidate))
        normalised_title = _normalise_title_for_identity(candidate.title)
        # "(title unavailable)" and friends must never merge unrelated
        # records, so only alias on a substantive title.
        if normalised_title and normalised_title != "title unavailable":
            title_key = (candidate.claim_id, normalised_title)
            aliased = title_alias.get(title_key)
            if aliased is not None and aliased != key:
                key = aliased
            else:
                title_alias[title_key] = key
        lane = candidate.discovery_lane
        existing = kept.get(key)
        if existing is None:
            kept[key] = candidate
            order.append(key)
            if lane:
                _lane_list(key).append(lane)
            continue

        if lane and lane not in _lane_list(key):
            _lane_list(key).append(lane)

        # Prefer the record that actually resolved canonical identifiers;
        # otherwise prefer a contradiction-tagged hit over a discovery one.
        replace = False
        if (
            not existing.canonical_identifiers
            and candidate.canonical_identifiers
        ):
            replace = True
        elif (
            existing.purpose != "contradiction"
            and candidate.purpose == "contradiction"
        ):
            replace = True
        if replace:
            kept[key] = candidate

    result: list[CandidateSource] = []
    for key in order:
        candidate = kept[key]
        found_by = lanes.get(key) or []
        if len(found_by) > 1:
            candidate = candidate.model_copy(
                update={"discovery_lane": ",".join(found_by)}
            )
        result.append(candidate)
    return result


def formatted_citation(candidate: CandidateSource) -> str:
    """Compute the formatted citation.

    Args:
        candidate (CandidateSource): The candidate.

    Returns:
        str: The citation.
    """
    author = ""
    if candidate.authors:
        author = candidate.authors[0]
        if len(candidate.authors) > 1:
            author += " et al."
    pieces = [item for item in (author, candidate.title, str(candidate.year or "")) if item]
    citation = ". ".join(pieces)
    if candidate.doi:
        citation += f". https://doi.org/{candidate.doi}"
    elif candidate.canonical_url:
        citation += f". {candidate.canonical_url}"
    return citation.strip().rstrip(".") + "."


def download_path_from_payload(raw: Any) -> str | None:
    """Download the path from payload.

    Args:
        raw (Any): Raw value.

    Returns:
        str | None: The path from payload.
    """
    payload = parse_mcp_payload(raw)
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("path", "file_path", "download_path", "result"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def validate_downloaded_pdf(
    raw_path: str | None,
    *,
    allowed_root: Path,
    max_bytes: int,
) -> Path:
    """Validate the downloaded pdf.

    Args:
        raw_path (str | None): The raw path.
        allowed_root (Path): The allowed root.
        max_bytes (int): The max bytes.

    Returns:
        Path: The downloaded pdf.
    """
    if not raw_path:
        raise ValueError("download tool did not return a file path")
    candidate = Path(raw_path).expanduser().resolve()
    root = allowed_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("download tool returned a path outside the evidence workspace")
    if not candidate.is_file():
        raise ValueError("download tool did not create a readable file")
    size = candidate.stat().st_size
    if size <= 4 or size > max_bytes:
        raise ValueError(f"downloaded file size {size} is outside policy")
    with candidate.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError("downloaded source is not a PDF")
    return candidate


def _tokens(text: str) -> list[str]:
    """Internal helper for the tokens step.

    Args:
        text (str): The text.

    Returns:
        list[str]: The result.
    """
    return [
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if len(token) > 2 and token.lower() not in _STOP_WORDS
    ]


def _page_windows(text: str, size: int = 1800, overlap: int = 250) -> list[str]:
    """Internal helper for the page windows step.

    Args:
        text (str): The text.
        size (int): The size (optional, default 1800).
        overlap (int): The overlap (optional, default 250).

    Returns:
        list[str]: The windows.
    """
    clean = re.sub(r"[ \t]+", " ", text or "").strip()
    if not clean:
        return []
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", clean) if item.strip()]
    if len(paragraphs) > 1 and max(map(len, paragraphs)) <= size * 2:
        windows: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if current and len(current) + len(paragraph) + 2 > size:
                windows.append(current)
                current = current[-overlap:] + "\n\n" + paragraph
            else:
                current = f"{current}\n\n{paragraph}".strip()
        if current:
            windows.append(current)
        return windows
    step = max(1, size - overlap)
    return [clean[index:index + size] for index in range(0, len(clean), step)]


def retrieve_passages(
    claim: str,
    pages: list[dict[str, Any]],
    *,
    document_id: str,
    limit: int = 8,
) -> list[RetrievedPassage]:
    """Page-aware lexical retrieval before semantic stance verification."""

    claim_tokens = _tokens(claim)
    query_counts = Counter(claim_tokens)
    claim_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", claim))
    ranked: list[tuple[float, int, str]] = []
    for page in pages:
        page_number = int(page.get("page_num") or page.get("page") or 0)
        if page_number < 1:
            continue
        for window in _page_windows(str(page.get("text") or "")):
            counts = Counter(_tokens(window))
            overlap = sum(min(counts[token], count) for token, count in query_counts.items())
            coverage = overlap / max(1, sum(query_counts.values()))
            density = overlap / max(1, sum(counts.values())) * 6
            number_bonus = 0.25 * len(
                claim_numbers.intersection(
                    re.findall(r"\b\d+(?:\.\d+)?%?\b", window)
                )
            )
            phrase_bonus = 0.5 if claim.lower() in window.lower() else 0.0
            score = coverage + density + number_bonus + phrase_bonus
            if score > 0:
                ranked.append((score, page_number, window))

    ranked.sort(key=lambda item: item[0], reverse=True)
    passages: list[RetrievedPassage] = []
    seen_hashes: set[str] = set()
    for score, page_number, text in ranked:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        passages.append(
            RetrievedPassage(
                passage_id=stable_id("PASS", document_id, page_number, digest),
                document_id=document_id,
                page=page_number,
                text=text,
                context=text,
                retrieval_score=round(score, 6),
                passage_sha256=digest,
            )
        )
        if len(passages) >= limit:
            break
    return passages
