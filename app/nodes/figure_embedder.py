"""Splice generated figures into compiled proposal text before rendering.

HorizonDOCXProposalRenderer (app/tools/docx_proposal_rendering.py) only
embeds an image from an inline ``data:image/...;base64,...`` URI already
present in the content it's given — it deliberately never fetches a remote
URL. An LLM drafting node can't reliably paste a multi-KB base64 blob into
its own prose, so this node does the substitution deterministically: each
drafting node is instructed to emit the existing, already-sanitiser-aware
``[[IMAGE PROMPT: <marker>]]`` placeholder (app/tools/proposal_rendering.py)
verbatim at the point a figure belongs, and this node replaces the markers
it has a matching image for with the real data URI. Any marker this node
doesn't recognise, or can't find, safely falls through unchanged and
degrades to the sanitiser's normal placeholder box.
"""
from __future__ import annotations

import asyncio
import base64
import re
from typing import Any

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


class FigureSpec(BaseModel):
    # Exact text inside "[[IMAGE PROMPT: <marker>]]" the drafting node was
    # instructed to emit verbatim — matched case-insensitively.
    marker: str
    # The WHOLE upstream image-generation node's output object (e.g.
    # OpenAIImageGenerationAgent's {generated, minio_key, content_type, ...}),
    # or None. Deliberately not split into separate minio_key/content_type
    # fields: this templating engine has no optional-chaining, so
    # "{{inputs.figure.minio_key}}" crashes outright if the whole `figure`
    # input was ever absent rather than merely disabled. Passing the whole
    # object with one "{{inputs.figure}}" whole-value reference and doing the
    # None-safe field lookup here in Python sidesteps that entirely.
    #
    # ``str`` is accepted only so an unresolved "{{...}}" template literal in
    # the raw YAML config passes structural preflight before the runtime
    # substitutes the real value — see WorkflowFileLoader.files /
    # KimiVisionConfig.image for the same pattern. run() treats a str here
    # (which should never actually reach it once resolved) the same as None.
    image: str | dict[str, Any] | None = None
    alt_text: str = "Proposal figure"


class FigureEmbedderInput(BaseModel):
    pass


class FigureEmbedderConfig(BaseModel):
    content: str
    figures: list[FigureSpec] = Field(default_factory=list)


class FigureEmbedderOutput(BaseModel):
    content: str
    embedded_count: int = 0
    # Configured figures whose marker never appeared in `content` — usually
    # means the drafting node paraphrased instead of copying it verbatim, or
    # the figure was optional and the drafter judged it unnecessary.
    unmatched_figures: list[str] = Field(default_factory=list)
    # Configured figures whose marker matched but minio_key couldn't be
    # read — the marker is left in place, so it still renders as a
    # placeholder rather than silently vanishing.
    missing_images: list[str] = Field(default_factory=list)


def _marker_pattern(marker: str) -> re.Pattern[str]:
    return re.compile(
        r"\[\[IMAGE PROMPT:\s*" + re.escape(marker) + r"\s*\]\]",
        re.IGNORECASE,
    )


@NodeRegistry.register
class FigureEmbedder(NodeType):
    type_name = "FigureEmbedder"
    description = (
        "Replace [[IMAGE PROMPT: marker]] placeholders with real embedded "
        "images read from object storage, before the proposal is rendered."
    )
    input_schema = FigureEmbedderInput
    config_schema = FigureEmbedderConfig
    output_schema = FigureEmbedderOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = FigureEmbedderConfig(**resolved_config)
        store = self.services.get("object_store")
        if store is None and cfg.figures:
            raise RuntimeError("FigureEmbedder requires object_store")

        content = cfg.content
        embedded_count = 0
        unmatched: list[str] = []
        missing: list[str] = []

        for figure in cfg.figures:
            pattern = _marker_pattern(figure.marker)
            if not pattern.search(content):
                unmatched.append(figure.marker)
                continue
            image = figure.image if isinstance(figure.image, dict) else {}
            minio_key = image.get("minio_key")
            content_type = image.get("content_type") or "image/png"
            if not minio_key:
                # Upstream generation was disabled, failed, or the figure
                # input was never supplied at all — leave the marker in
                # place for the existing placeholder fallback.
                missing.append(figure.marker)
                continue
            try:
                raw = await asyncio.to_thread(store.get_bytes, minio_key)
            except Exception:
                missing.append(figure.marker)
                continue
            data_uri = (
                f"data:{content_type};base64,"
                f"{base64.b64encode(raw).decode('ascii')}"
            )
            replacement = f"![{figure.alt_text}]({data_uri})"
            content = pattern.sub(replacement, content, count=1)
            embedded_count += 1

        return {
            "content": content,
            "embedded_count": embedded_count,
            "unmatched_figures": unmatched,
            "missing_images": missing,
        }
