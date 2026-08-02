"""Horizon proposal HTML/Markdown to PDF workflow node."""
from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger
from app.tools.content_length import narrative_char_count
from app.tools.proposal_rendering import render_horizon_proposal


log = get_logger(__name__)


class HorizonHTMLProposalRendererInput(BaseModel):
    pass


class HorizonHTMLProposalRendererConfig(BaseModel):
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


class HorizonHTMLProposalRendererOutput(BaseModel):
    minio_key: str
    pdf_key: str
    html_key: str
    byte_size: int
    html_byte_size: int
    page_count: int
    page_limit: int | None = None
    html_sha256: str
    pdf_sha256: str
    template_used: str
    template_version: str
    table_of_contents: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    submission_ready: bool


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")[:100] or "run"


@NodeRegistry.register
class HorizonHTMLProposalRenderer(NodeType):
    type_name = "HorizonHTMLProposalRenderer"
    description = (
        "Convert proposal HTML or Markdown into a citation-aware Horizon "
        "Europe Part B PDF with cover, TOC, page checks, and evidence annex."
    )
    input_schema = HorizonHTMLProposalRendererInput
    config_schema = HorizonHTMLProposalRendererConfig
    output_schema = HorizonHTMLProposalRendererOutput

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"object_store"}

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = HorizonHTMLProposalRendererConfig(**resolved_config)
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
                "proposal renderer templates did not resolve for: "
                + ", ".join(unresolved)
            )
        store = self.services.get("object_store")
        if store is None:
            raise RuntimeError(
                "HorizonHTMLProposalRenderer requires object_store"
            )
        if narrative_char_count(cfg.content) > cfg.max_content_characters:
            raise ValueError(
                f"proposal content exceeds {cfg.max_content_characters} characters"
            )
        rendered = await asyncio.to_thread(
            render_horizon_proposal,
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
        )
        if (
            cfg.enforce_page_limit
            and cfg.page_limit is not None
            and rendered.page_count > cfg.page_limit
        ):
            raise ValueError(
                f"rendered proposal has {rendered.page_count} pages; "
                f"limit is {cfg.page_limit}"
            )

        run_id = _safe_component(
            str(
                state.get("inputs", {}).get("SYSTEM.run_id")
                or "manual"
            )
        )
        base = f"workflows/{run_id}/proposal"
        pdf_key = f"{base}.pdf"
        html_key = f"{base}.html"
        html_bytes = rendered.html.encode("utf-8")
        await asyncio.to_thread(
            store.put_bytes,
            rendered.pdf,
            pdf_key,
            content_type="application/pdf",
        )
        await asyncio.to_thread(
            store.put_bytes,
            html_bytes,
            html_key,
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
            "horizon_pdf.rendered",
            node_id=self.node_id,
            run_id=run_id,
            pdf_key=pdf_key,
            html_key=html_key,
            page_count=rendered.page_count,
            warnings=len(rendered.warnings),
        )
        return {
            "minio_key": pdf_key,
            "pdf_key": pdf_key,
            "html_key": html_key,
            "byte_size": len(rendered.pdf),
            "html_byte_size": len(html_bytes),
            "page_count": rendered.page_count,
            "page_limit": cfg.page_limit,
            "html_sha256": rendered.html_sha256,
            "pdf_sha256": rendered.pdf_sha256,
            "template_used": "horizon_part_b",
            "template_version": "2.0",
            "table_of_contents": rendered.table_of_contents,
            "warnings": rendered.warnings,
            "submission_ready": submission_ready,
        }
