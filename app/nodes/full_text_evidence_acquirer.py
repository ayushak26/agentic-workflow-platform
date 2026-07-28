"""Lawful, bounded full-text acquisition for scholarly candidates."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, Field

from app.evidence.models import (
    CandidateSource,
    EvidencePolicy,
    FullTextDocument,
    RejectedCandidate,
)
from app.evidence.retrieval import (
    download_path_from_payload,
    formatted_citation,
    stable_id,
    utc_now,
    validate_downloaded_pdf,
)
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.tools.pdf_io import extract_text_from_pdf


class FullTextEvidenceAcquirerInput(BaseModel):
    pass


class FullTextEvidenceAcquirerConfig(BaseModel):
    # A whole-value runtime template is a string during workflow compilation
    # and resolves to the typed list immediately before run().
    candidates: str | list[CandidateSource]
    policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    mcp_server: str = "paper-search-mcp"
    download_tool: str = "download_with_fallback"
    fail_when_none_acquired: bool = False


class FullTextEvidenceAcquirerOutput(BaseModel):
    candidates_processed: int = 0
    full_text_documents_acquired: int = 0
    documents: list[FullTextDocument] = Field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    report: str = ""


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")[:80] or "run"


@NodeRegistry.register
class FullTextEvidenceAcquirer(NodeType):
    type_name = "FullTextEvidenceAcquirer"
    description = (
        "Download lawful open-access PDFs for candidate sources, extract "
        "page-aware text, and store immutable source versions."
    )
    input_schema = FullTextEvidenceAcquirerInput
    config_schema = FullTextEvidenceAcquirerConfig
    output_schema = FullTextEvidenceAcquirerOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = FullTextEvidenceAcquirerConfig(**resolved_config)
        if isinstance(cfg.candidates, str):
            raise ValueError(
                "candidates template did not resolve to a candidate list"
            )
        mcp = self.services.get("mcp_client")
        store = self.services.get("object_store")
        if mcp is None or store is None:
            missing = [
                name
                for name, service in (
                    ("mcp_client", mcp),
                    ("object_store", store),
                )
                if service is None
            ]
            raise RuntimeError(
                f"FullTextEvidenceAcquirer requires services {missing}"
            )

        run_id = str(
            state.get("inputs", {}).get("SYSTEM.run_id") or "manual"
        )
        run_component = _safe_component(run_id)
        counts: dict[str, int] = defaultdict(int)
        documents: list[FullTextDocument] = []
        rejected: list[RejectedCandidate] = []

        with TemporaryDirectory(
            prefix=f"eurskem-evidence-{run_component}-"
        ) as temporary:
            root = Path(temporary).resolve()
            for candidate in cfg.candidates:
                if (
                    counts[candidate.claim_id]
                    >= cfg.policy.max_full_text_documents_per_claim
                ):
                    continue
                rejection = self._pre_download_rejection(candidate)
                if rejection:
                    rejected.append(rejection)
                    continue

                candidate_dir = root / _safe_component(candidate.candidate_id)
                candidate_dir.mkdir(parents=True, exist_ok=True)
                try:
                    raw_result = await mcp.call_tool(
                        name=cfg.download_tool,
                        arguments={
                            "source": candidate.source,
                            "paper_id": candidate.paper_id or "",
                            "doi": candidate.doi or "",
                            "title": candidate.title,
                            "save_path": str(candidate_dir),
                            # Eurskem uses lawful OA retrieval only.
                            "use_scihub": False,
                        },
                        server=cfg.mcp_server,
                    )
                    local_pdf = validate_downloaded_pdf(
                        download_path_from_payload(raw_result),
                        allowed_root=candidate_dir,
                        max_bytes=cfg.policy.max_download_bytes,
                    )
                    raw_pdf = await asyncio.to_thread(local_pdf.read_bytes)
                    pages = await asyncio.to_thread(
                        extract_text_from_pdf,
                        raw_pdf,
                    )
                    usable_pages = [
                        page for page in pages if str(page.get("text") or "").strip()
                    ]
                    if not usable_pages:
                        raise ValueError(
                            "PDF contains no extractable text; OCR/manual review required"
                        )

                    digest = hashlib.sha256(raw_pdf).hexdigest()
                    version_id = f"VER-{digest[:16]}"
                    source_id = stable_id(
                        "SRC",
                        candidate.doi
                        or candidate.paper_id
                        or candidate.canonical_url
                        or candidate.title,
                    )
                    base_key = (
                        f"evidence/{run_component}/{source_id}/{version_id}"
                    )
                    pdf_key = f"{base_key}.pdf"
                    pages_key = f"{base_key}.pages.json"
                    pages_payload = json.dumps(
                        {
                            "source_id": source_id,
                            "version_id": version_id,
                            "content_sha256": digest,
                            "pages": pages,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    await asyncio.to_thread(
                        store.put_bytes,
                        raw_pdf,
                        pdf_key,
                        content_type="application/pdf",
                    )
                    await asyncio.to_thread(
                        store.put_bytes,
                        pages_payload,
                        pages_key,
                        content_type="application/json",
                    )
                    document = FullTextDocument(
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
                            else candidate.paper_id
                            or candidate.canonical_url
                        ),
                        canonical_url=(
                            f"https://doi.org/{candidate.doi}"
                            if candidate.doi
                            else candidate.canonical_url
                        ),
                        source_type=candidate.source,
                        authority=candidate.authority,
                        independence_group=candidate.independence_group,
                        version_id=version_id,
                        content_sha256=digest,
                        pdf_object_key=pdf_key,
                        pages_object_key=pages_key,
                        page_count=len(pages),
                        canonical_metadata_validated=(
                            candidate.metadata_status == "canonical"
                        ),
                        retraction_status=candidate.retraction_status,
                        fetched_at=utc_now(),
                    )
                    documents.append(document)
                    counts[candidate.claim_id] += 1
                except Exception as exc:
                    rejected.append(
                        RejectedCandidate(
                            claim_id=candidate.claim_id,
                            candidate_title=candidate.title,
                            candidate_identifier=(
                                candidate.doi
                                or candidate.paper_id
                                or candidate.canonical_url
                            ),
                            reason="full_text_unavailable",
                            notes=f"{type(exc).__name__}: {exc}"[:500],
                        )
                    )

        if cfg.fail_when_none_acquired and cfg.candidates and not documents:
            raise RuntimeError(
                "No candidate passed lawful full-text acquisition. "
                "Inspect rejected_candidates before retrying."
            )
        return {
            "candidates_processed": len(cfg.candidates),
            "full_text_documents_acquired": len(documents),
            "documents": [
                item.model_dump(mode="json") for item in documents
            ],
            "rejected_candidates": [
                item.model_dump(mode="json") for item in rejected
            ],
            "report": (
                f"Acquired {len(documents)} immutable full-text source "
                f"versions; rejected {len(rejected)} candidates. Abstracts "
                "and search snippets were not promoted to evidence."
            ),
        }

    @staticmethod
    def _pre_download_rejection(
        candidate: CandidateSource,
    ) -> RejectedCandidate | None:
        if candidate.retraction_status == "retracted":
            return RejectedCandidate(
                claim_id=candidate.claim_id,
                candidate_title=candidate.title,
                candidate_identifier=(
                    candidate.doi
                    or candidate.paper_id
                    or candidate.canonical_url
                ),
                reason="retracted_source",
                notes="Retracted sources cannot provide affirmative support.",
            )
        if not (
            candidate.paper_id
            or candidate.doi
            or candidate.canonical_url
            or candidate.pdf_url
        ):
            return RejectedCandidate(
                claim_id=candidate.claim_id,
                candidate_title=candidate.title,
                reason="missing_download_identifier",
            )
        return None
