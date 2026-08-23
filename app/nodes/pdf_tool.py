"""PDF tool node — two pre-baked variants:
  - PDFTextExtractor: read .pdf, return text per page
  - PDFProposalRenderer: render sections to styled .pdf
"""
from __future__ import annotations
import asyncio
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger
from app.tools.pdf_io import extract_text_from_pdf, render_proposal_pdf

log = get_logger(__name__)


# ============================================================
#  PDFTextExtractor
# ============================================================

class PDFExtractInput(BaseModel):
    """Pydantic model defining the PDFExtractInput shape."""
    pass


class PDFExtractConfig(BaseModel):
    """Pydantic model defining the PDFExtractConfig shape.

    Attributes:
        minio_key (str).
    """
    minio_key: str


class PDFExtractOutput(BaseModel):
    """Pydantic model defining the PDFExtractOutput shape.

    Attributes:
        pages (list[dict[str, Any]]).
        page_count (int).
    """
    pages: list[dict[str, Any]]
    page_count: int


@NodeRegistry.register
class PDFTextExtractor(NodeType):
    """Workflow node type implementing the PDFTextExtractor capability."""
    type_name = "PDFTextExtractor"
    description = "Extract text from a .pdf in object storage."
    input_schema = PDFExtractInput
    output_schema = PDFExtractOutput
    config_schema = PDFExtractConfig

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return {"object_store"}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        store = self.services["object_store"]
        cfg = PDFExtractConfig(**resolved_config)
        raw = await asyncio.to_thread(store.get_bytes, cfg.minio_key)
        pages = await asyncio.to_thread(extract_text_from_pdf, raw)
        log.info("pdf.extracted", node_id=self.node_id,
                 minio_key=cfg.minio_key, pages=len(pages))
        return {"pages": pages, "page_count": len(pages)}


# ============================================================
#  PDFProposalRenderer  — the flagship deliverable
# ============================================================

TemplateName = Literal["corporate", "professional", "warm"]


class PDFRenderInput(BaseModel):
    """Pydantic model defining the PDFRenderInput shape."""
    pass


class PDFRenderConfig(BaseModel):
    """Pydantic model defining the PDFRenderConfig shape.

    Attributes:
        sections (dict[str, str]).
        template (TemplateName).
        proposal_title (str).
        client_name (str).
    """
    sections: dict[str, str] = Field(
        description="Map of section_name -> section_text. Templated from upstream nodes."
    )
    template: TemplateName = "corporate"
    proposal_title: str
    client_name: str


class PDFRenderOutput(BaseModel):
    """Pydantic model defining the PDFRenderOutput shape.

    Attributes:
        minio_key (str).
        byte_size (int).
        template_used (str).
    """
    minio_key: str
    byte_size: int
    template_used: str


@NodeRegistry.register
class PDFProposalRenderer(NodeType):
    """Workflow node type implementing the PDFProposalRenderer capability."""
    type_name = "PDFProposalRenderer"
    description = "Render proposal sections to a styled PDF (corporate, professional, warm)."
    input_schema = PDFRenderInput
    output_schema = PDFRenderOutput
    config_schema = PDFRenderConfig

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return {"object_store"}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        store = self.services["object_store"]
        cfg = PDFRenderConfig(**resolved_config)

        # WeasyPrint is sync and CPU-bound — push to a thread
        pdf_bytes = await asyncio.to_thread(
            render_proposal_pdf,
            sections=cfg.sections,
            template=cfg.template,
            client_name=cfg.client_name,
            proposal_title=cfg.proposal_title,
        )

        # Upload to MinIO under a workflow-scoped key
        run_id = state.get("inputs", {}).get("SYSTEM.run_id", str(uuid.uuid4()))
        minio_key = f"workflows/{run_id}/proposal.pdf"
        await asyncio.to_thread(
            store.put_bytes,
            pdf_bytes,
            minio_key,
            content_type="application/pdf",
        )

        log.info("pdf.rendered", node_id=self.node_id,
                 minio_key=minio_key, byte_size=len(pdf_bytes),
                 template=cfg.template)
        return {
            "minio_key": minio_key,
            "byte_size": len(pdf_bytes),
            "template_used": cfg.template,
        }