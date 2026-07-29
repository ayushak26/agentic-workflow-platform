"""Horizon proposal HTML/Markdown to editable DOCX workflow node."""
from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger
from app.tools.docx_proposal_rendering import (
    DOCX_CONTENT_TYPE,
    render_horizon_proposal_docx,
)


log = get_logger(__name__)


class HorizonDOCXProposalRendererInput(BaseModel):
    pass


class HorizonDOCXProposalRendererConfig(BaseModel):
    content: str
    content_format: Literal["markdown", "html"] = "markdown"
    metadata: str | dict[str, Any] = Field(default_factory=dict)
    citation_registry: str | list[dict[str, Any]] = Field(default_factory=list)
    evidence_qa: str | dict[str, Any] = Field(default_factory=dict)
    evidence_blockers: str | list[str] = Field(default_factory=list)
    include_toc: bool = True
    include_bibliography: bool = True
    include_evidence_annex: bool = False
    page_limit: int | None = Field(default=45, ge=1, le=500)
    enforce_page_limit: bool = False
    max_content_characters: int = Field(
        default=2_000_000,
        ge=1_000,
        le=10_000_000,
    )
    max_embedded_image_bytes: int = Field(
        default=10_000_000,
        ge=1_000,
        le=50_000_000,
    )


class HorizonDOCXProposalRendererOutput(BaseModel):
    minio_key: str
    docx_key: str
    source_html_key: str
    byte_size: int
    source_html_byte_size: int
    estimated_page_count: int
    page_count: int
    page_count_basis: str
    page_limit: int | None = None
    docx_sha256: str
    source_html_sha256: str
    template_used: str
    template_version: str
    table_of_contents: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    embedded_image_count: int
    image_placeholder_count: int
    submission_ready: bool


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")[:100] or "run"


@NodeRegistry.register
class HorizonDOCXProposalRenderer(NodeType):
    type_name = "HorizonDOCXProposalRenderer"
    description = (
        "Convert proposal HTML or Markdown into an editable, citation-aware "
        "Horizon Europe Part B DOCX with native headings, TOC, tables, "
        "figures, page fields, page-limit estimate, and evidence annex."
    )
    input_schema = HorizonDOCXProposalRendererInput
    config_schema = HorizonDOCXProposalRendererConfig
    output_schema = HorizonDOCXProposalRendererOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = HorizonDOCXProposalRendererConfig(**resolved_config)
        unresolved = [
            name
            for name, value in {
                "metadata": cfg.metadata,
                "citation_registry": cfg.citation_registry,
                "evidence_qa": cfg.evidence_qa,
                "evidence_blockers": cfg.evidence_blockers,
            }.items()
            if isinstance(value, str)
        ]
        if unresolved:
            raise ValueError(
                "DOCX proposal renderer templates did not resolve for: "
                + ", ".join(unresolved)
            )
        store = self.services.get("object_store")
        if store is None:
            raise RuntimeError(
                "HorizonDOCXProposalRenderer requires object_store"
            )
        if len(cfg.content) > cfg.max_content_characters:
            raise ValueError(
                f"proposal content exceeds {cfg.max_content_characters} characters"
            )

        rendered = await asyncio.to_thread(
            render_horizon_proposal_docx,
            content=cfg.content,
            content_format=cfg.content_format,
            metadata=cfg.metadata,
            citation_registry=cfg.citation_registry,
            evidence_qa=cfg.evidence_qa,
            evidence_blockers=cfg.evidence_blockers,
            include_toc=cfg.include_toc,
            include_bibliography=cfg.include_bibliography,
            include_evidence_annex=cfg.include_evidence_annex,
            page_limit=cfg.page_limit,
            max_embedded_image_bytes=cfg.max_embedded_image_bytes,
        )
        if (
            cfg.enforce_page_limit
            and cfg.page_limit is not None
            and rendered.estimated_page_count > cfg.page_limit
        ):
            raise ValueError(
                "PDF-equivalent page estimate is "
                f"{rendered.estimated_page_count} pages; "
                f"limit is {cfg.page_limit}"
            )

        run_id = _safe_component(
            str(
                state.get("inputs", {}).get("SYSTEM.run_id")
                or "manual"
            )
        )
        base = f"workflows/{run_id}/proposal"
        docx_key = f"{base}.docx"
        source_html_key = f"{base}.docx-source.html"
        source_html_bytes = rendered.source_html.encode("utf-8")
        await asyncio.to_thread(
            store.put_bytes,
            rendered.docx,
            docx_key,
            content_type=DOCX_CONTENT_TYPE,
        )
        await asyncio.to_thread(
            store.put_bytes,
            source_html_bytes,
            source_html_key,
            content_type="text/html; charset=utf-8",
        )

        submission_ready = (
            not cfg.evidence_blockers
            and not any(
                warning.startswith("Rendered proposal is")
                for warning in rendered.warnings
            )
            and not any(
                "INPUT NEEDED" in warning
                for warning in rendered.warnings
            )
        )
        log.info(
            "horizon_docx.rendered",
            node_id=self.node_id,
            run_id=run_id,
            docx_key=docx_key,
            estimated_page_count=rendered.estimated_page_count,
            page_count_basis=rendered.page_count_basis,
            warnings=len(rendered.warnings),
        )
        return {
            "minio_key": docx_key,
            "docx_key": docx_key,
            "source_html_key": source_html_key,
            "byte_size": len(rendered.docx),
            "source_html_byte_size": len(source_html_bytes),
            "estimated_page_count": rendered.estimated_page_count,
            # Backward-compatible field for the current Output Viewer.  The
            # basis is explicit so consumers never treat it as Word pagination.
            "page_count": rendered.estimated_page_count,
            "page_count_basis": rendered.page_count_basis,
            "page_limit": cfg.page_limit,
            "docx_sha256": rendered.docx_sha256,
            "source_html_sha256": rendered.source_html_sha256,
            "template_used": "horizon_part_b_docx",
            "template_version": "1.0",
            "table_of_contents": rendered.table_of_contents,
            "warnings": rendered.warnings,
            "embedded_image_count": rendered.embedded_image_count,
            "image_placeholder_count": rendered.image_placeholder_count,
            "submission_ready": submission_ready,
        }
