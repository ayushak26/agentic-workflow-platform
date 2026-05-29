"""PowerPoint tool node — pre-baked variant: generate slide deck from sections."""
from __future__ import annotations
import asyncio
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger
from app.tools.powerpoint_io import build_proposal_pptx

log = get_logger(__name__)


class PPTInput(BaseModel):
    pass


class PPTConfig(BaseModel):
    sections: dict[str, str]
    proposal_title: str
    client_name: str


class PPTOutput(BaseModel):
    minio_key: str
    slide_count: int
    byte_size: int


@NodeRegistry.register
class PowerPointProposalSlides(NodeType):
    type_name = "PowerPointProposalSlides"
    description = "Build a .pptx deck from proposal sections."
    input_schema = PPTInput
    output_schema = PPTOutput
    config_schema = PPTConfig

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        store = self.services["object_store"]
        cfg = PPTConfig(**resolved_config)

        pptx_bytes = await asyncio.to_thread(
            build_proposal_pptx,
            sections=cfg.sections,
            title=cfg.proposal_title,
            client_name=cfg.client_name,
        )

        run_id = state.get("inputs", {}).get("SYSTEM.run_id", str(uuid.uuid4()))
        minio_key = f"workflows/{run_id}/proposal.pptx"
        await asyncio.to_thread(
            store.put_bytes,
            pptx_bytes,
            minio_key,
            content_type=("application/vnd.openxmlformats-officedocument."
                          "presentationml.presentation"),
        )

        # +1 for the title slide
        slide_count = len(cfg.sections) + 1
        log.info("pptx.built", node_id=self.node_id,
                 minio_key=minio_key, slides=slide_count, byte_size=len(pptx_bytes))
        return {
            "minio_key": minio_key,
            "slide_count": slide_count,
            "byte_size": len(pptx_bytes),
        }