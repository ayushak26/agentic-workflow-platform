"""Acquire Deep Research citations directly, without a paper-search MCP."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
from collections import defaultdict
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, field_validator

from app.evidence.models import (
    CandidateSource,
    EvidencePolicy,
    FullTextDocument,
    RejectedCandidate,
    coerce_typed_list_field,
)
from app.evidence.retrieval import formatted_citation, stable_id, utc_now
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.tools.pdf_io import extract_text_from_pdf


class ResearchSourceAcquirerInput(BaseModel):
    pass


class ResearchSourceAcquirerConfig(BaseModel):
    candidates: str | list[CandidateSource] | list[str]
    policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    max_concurrent_requests: int = Field(default=6, ge=1, le=12)
    max_sources_per_claim: int = Field(default=4, ge=1, le=10)
    max_total_sources: int = Field(default=60, ge=1, le=150)
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=180)
    max_redirects: int = Field(default=4, ge=0, le=8)
    fail_when_none_acquired: bool = False

    @field_validator("candidates", mode="before")
    @classmethod
    def _coerce_candidates(cls, value: Any) -> Any:
        return coerce_typed_list_field(value, CandidateSource, "candidates")


class ResearchSourceAcquirerOutput(BaseModel):
    candidates_processed: int = 0
    full_text_documents_acquired: int = 0
    documents: list[FullTextDocument] = Field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    report: str = ""


@NodeRegistry.register
class ResearchSourceAcquirer(NodeType):
    type_name = "ResearchSourceAcquirer"
    description = (
        "Resolve and store bounded Deep Research citations as immutable HTML "
        "or PDF source versions for exact-passage verification."
    )
    input_schema = ResearchSourceAcquirerInput
    config_schema = ResearchSourceAcquirerConfig
    output_schema = ResearchSourceAcquirerOutput

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"object_store"}

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = ResearchSourceAcquirerConfig(**resolved_config)
        if isinstance(cfg.candidates, str):
            raise ValueError(
                "candidates template did not resolve to a candidate list"
            )
        store = self.services.get("object_store")
        if store is None:
            raise RuntimeError(
                "ResearchSourceAcquirer requires object_store"
            )

        selected = _bounded_candidates(
            cfg.candidates,
            per_claim=cfg.max_sources_per_claim,
            total=cfg.max_total_sources,
        )
        run_id = str(
            state.get("inputs", {}).get("SYSTEM.run_id") or "manual"
        )
        semaphore = asyncio.Semaphore(cfg.max_concurrent_requests)
        documents: list[FullTextDocument] = []
        rejected: list[RejectedCandidate] = []

        async with httpx.AsyncClient(
            timeout=cfg.request_timeout_seconds,
            headers={
                "User-Agent": (
                    "EurskemAI-EvidenceVerifier/1.0 "
                    "(lawful research source verification)"
                ),
                "Accept": "text/html,application/pdf,application/xhtml+xml",
            },
            follow_redirects=False,
        ) as client:

            async def _one(candidate: CandidateSource) -> None:
                async with semaphore:
                    try:
                        document = await _acquire_candidate(
                            candidate,
                            client=client,
                            store=store,
                            run_id=run_id,
                            policy=cfg.policy,
                            max_redirects=cfg.max_redirects,
                        )
                        documents.append(document)
                    except Exception as exc:
                        rejected.append(
                            RejectedCandidate(
                                claim_id=candidate.claim_id,
                                candidate_title=candidate.title,
                                candidate_identifier=(
                                    candidate.doi
                                    or candidate.canonical_url
                                ),
                                reason="full_text_unavailable",
                                notes=f"{type(exc).__name__}: {exc}"[:500],
                            )
                        )

            await asyncio.gather(*(_one(candidate) for candidate in selected))

        documents.sort(key=lambda item: item.document_id)
        rejected.sort(
            key=lambda item: (item.claim_id, item.candidate_title)
        )
        if cfg.fail_when_none_acquired and selected and not documents:
            raise RuntimeError(
                "No Deep Research citation passed lawful full-text "
                "acquisition. Inspect rejected_candidates before retrying."
            )
        return ResearchSourceAcquirerOutput(
            candidates_processed=len(selected),
            full_text_documents_acquired=len(documents),
            documents=documents,
            rejected_candidates=rejected,
            report=(
                f"Acquired {len(documents)} immutable cited source version(s) "
                f"directly from canonical URLs; rejected {len(rejected)}. "
                "No search snippet or Deep Research prose was promoted to "
                "verified evidence."
            ),
        ).model_dump(mode="json")


async def _acquire_candidate(
    candidate: CandidateSource,
    *,
    client: httpx.AsyncClient,
    store: Any,
    run_id: str,
    policy: EvidencePolicy,
    max_redirects: int,
) -> FullTextDocument:
    source_url = candidate.pdf_url or candidate.canonical_url
    if not source_url:
        raise ValueError("candidate has no canonical URL")
    final_url, content_type, raw = await _fetch_public_source(
        client,
        source_url,
        max_bytes=policy.max_download_bytes,
        max_redirects=max_redirects,
    )
    is_pdf = raw.startswith(b"%PDF") or "application/pdf" in content_type
    if is_pdf:
        pages = await asyncio.to_thread(extract_text_from_pdf, raw)
        extension = "pdf"
        stored_content_type = "application/pdf"
    else:
        pages = _html_pages(raw)
        extension = "html"
        stored_content_type = "text/html; charset=utf-8"
    pages = [
        page
        for page in pages
        if str(page.get("text") or "").strip()
    ]
    if not pages:
        raise ValueError("source has no extractable text")

    canonical_ok, retraction_status = await _metadata_status(
        candidate,
        final_url=final_url,
        client=client,
    )
    digest = hashlib.sha256(raw).hexdigest()
    version_id = f"VER-{digest[:16]}"
    source_id = stable_id(
        "SRC",
        candidate.doi or final_url or candidate.title,
    )
    run_component = stable_id("RUN", run_id, length=12)
    base_key = f"evidence/{run_component}/{source_id}/{version_id}"
    raw_key = f"{base_key}.{extension}"
    pages_key = f"{base_key}.pages.json"
    pages_payload = json.dumps(
        {
            "source_id": source_id,
            "version_id": version_id,
            "content_sha256": digest,
            "canonical_url": final_url,
            "pages": pages,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    await asyncio.to_thread(
        store.put_bytes,
        raw,
        raw_key,
        content_type=stored_content_type,
    )
    await asyncio.to_thread(
        store.put_bytes,
        pages_payload,
        pages_key,
        content_type="application/json",
    )
    return FullTextDocument(
        document_id=stable_id(
            "DOC",
            candidate.candidate_id,
            version_id,
        ),
        candidate_id=candidate.candidate_id,
        claim_id=candidate.claim_id,
        title=candidate.title,
        citation=formatted_citation(candidate),
        identifier=(
            f"doi:{candidate.doi}"
            if candidate.doi
            else final_url
        ),
        canonical_url=final_url,
        source_type=candidate.source,
        authority=candidate.authority,
        independence_group=candidate.independence_group,
        version_id=version_id,
        content_sha256=digest,
        pdf_object_key=raw_key,
        pages_object_key=pages_key,
        page_count=len(pages),
        canonical_metadata_validated=canonical_ok,
        retraction_status=retraction_status,
        fetched_at=utc_now(),
    )


def _bounded_candidates(
    candidates: list[CandidateSource],
    *,
    per_claim: int,
    total: int,
) -> list[CandidateSource]:
    by_claim: dict[str, list[CandidateSource]] = defaultdict(list)
    for item in candidates:
        by_claim[item.claim_id].append(item)
    selected: list[CandidateSource] = []
    for claim_id in sorted(by_claim):
        group = by_claim[claim_id]
        contradiction = [
            item for item in group if item.purpose == "contradiction"
        ]
        discovery = [
            item for item in group if item.purpose != "contradiction"
        ]
        selected.extend((contradiction[:1] + discovery)[:per_claim])
        if len(selected) >= total:
            return selected[:total]
    return selected[:total]


async def _fetch_public_source(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    max_redirects: int,
) -> tuple[str, str, bytes]:
    current = url
    for redirect_count in range(max_redirects + 1):
        await _require_public_url(current)
        async with client.stream("GET", current) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("redirect response has no Location")
                if redirect_count >= max_redirects:
                    raise ValueError("source exceeded redirect limit")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError("source exceeds maximum download size")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("source exceeds maximum download size")
                chunks.append(chunk)
            return (
                str(response.url),
                response.headers.get("content-type", "").lower(),
                b"".join(chunks),
            )
    raise ValueError("source exceeded redirect limit")


async def _require_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only public HTTP(S) sources are allowed")
    if not parsed.hostname:
        raise ValueError("source URL has no hostname")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".internal")
    ):
        raise ValueError("private host is not allowed")

    def _resolve() -> set[str]:
        return {
            item[4][0]
            for item in socket.getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }

    addresses = await asyncio.to_thread(_resolve)
    if not addresses:
        raise ValueError("source hostname did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("source resolves to a non-public address")


def _html_pages(raw: bytes, *, chars_per_page: int = 9000) -> list[dict[str, Any]]:
    soup = BeautifulSoup(raw, "html.parser")
    for element in soup(
        ["script", "style", "noscript", "svg", "nav", "footer", "form"]
    ):
        element.decompose()
    text = "\n".join(
        line.strip()
        for line in soup.get_text("\n").splitlines()
        if line.strip()
    )
    return [
        {
            "page": index + 1,
            "text": text[start : start + chars_per_page],
        }
        for index, start in enumerate(range(0, len(text), chars_per_page))
    ]


async def _metadata_status(
    candidate: CandidateSource,
    *,
    final_url: str,
    client: httpx.AsyncClient,
) -> tuple[bool, str]:
    host = (urlsplit(final_url).hostname or "").lower()
    if candidate.authority == "official_eu" or host.endswith(
        (".europa.eu", "europa.eu")
    ):
        return True, "clear"
    if not candidate.doi:
        return bool(candidate.title and final_url), "unchecked"

    endpoint = (
        "https://api.crossref.org/works/"
        + quote(candidate.doi, safe="")
    )
    try:
        response = await client.get(
            endpoint,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        message = response.json().get("message") or {}
        title = message.get("title") or []
        canonical = bool(title and message.get("DOI"))
        relation = message.get("relation") or {}
        retracted = any(
            "retract" in str(key).lower()
            for key in relation
        )
        return canonical, "retracted" if retracted else "clear"
    except Exception:
        return False, "unknown"
