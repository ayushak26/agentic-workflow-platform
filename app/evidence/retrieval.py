"""Deterministic helpers for discovery, full-text handling, and passages."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.evidence.models import CandidateSource, RetrievedPassage


_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*")
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "into", "is", "it", "of", "on", "or", "that",
    "the", "their", "this", "to", "was", "were", "will", "with",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: Any, length: int = 16) -> str:
    basis = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}-{digest}"


def normalise_doi(value: Any) -> str | None:
    if not value:
        return None
    doi = str(value).strip()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    return doi.lower() if _DOI_RE.fullmatch(doi) else None


def parse_authors(value: Any) -> list[str]:
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
    match = re.search(r"\b(19|20)\d{2}\b", str(value or ""))
    return int(match.group(0)) if match else None


def first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def parse_mcp_payload(raw: Any) -> Any:
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


def candidate_from_paper(
    paper: dict[str, Any],
    *,
    claim_id: str,
    query: str,
    purpose: str,
    source_hint: str | None = None,
) -> CandidateSource:
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
        metadata_status="canonical" if doi and title != "(title unavailable)" else "candidate",
        retraction_status=retraction_status,
        retrieved_at=utc_now(),
    )


def deduplicate_candidates(
    candidates: list[CandidateSource],
) -> list[CandidateSource]:
    """Deduplicate only within one claim while preserving contradiction intent."""

    seen: set[tuple[str, str, str]] = set()
    result: list[CandidateSource] = []
    for candidate in candidates:
        identity = candidate.doi or candidate.paper_id or candidate.title.lower()
        key = (candidate.claim_id, identity, candidate.purpose)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def formatted_citation(candidate: CandidateSource) -> str:
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
    return [
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if len(token) > 2 and token.lower() not in _STOP_WORDS
    ]


def _page_windows(text: str, size: int = 1800, overlap: int = 250) -> list[str]:
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
