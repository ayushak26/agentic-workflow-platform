"""DynamicFigureAgent: generate an image for every inline image marker and
embed it as a data URI the DOCX renderer can actually render.

WHY THIS EXISTS (root-cause of "no images in any document")
-----------------------------------------------------------
The DOCX renderer embeds a picture ONLY when it sees an <img> whose src is a
``data:image/...;base64,...`` URI (docx_proposal_rendering: line ~1406
``if not source.startswith("data:image/")`` -> ``add_picture``). But the old
OpenAIImageGenerationAgent returns a ``minio_key`` - NOT base64 - so the old
FigureEmbedder had no data URI to substitute, every marker "fell through
unchanged", and the document rendered placeholder boxes with no image.

This agent replaces the three fixed image nodes AND FigureEmbedder with one
node that:
  1. scans the FINAL content (after all LLM rewrite steps, so no rewrite can
     drift a marker) for every ``[[IMAGE PROMPT: <prompt>]]`` by REGEX - so it
     matches any content and cannot break on punctuation/rewording the way the
     old exact-string marker did;
  2. generates a diagram-styled image per marker from the inline prompt text;
  3. reads the image BYTES back and base64-encodes them into a
     ``data:image/png;base64,...`` URI (the exact shape the renderer wants);
  4. emits two content variants:
       - ``illustrated_content``: each marker -> <figure><img data-uri>...
         (this is what the NEW illustrated renderer consumes);
       - ``captioned_content``: each marker -> ``*Figure N: <prompt>*`` caption
         (this is what the text-only renderers consume, so no raw markers leak).

Deterministic except the image API calls. One node, internal loop (no fan-out
primitive in the compiler). max_images bounds cost/latency - image generation
is the most expensive per-call step in the graph.
"""
from __future__ import annotations

import asyncio
import base64
import re
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger

log = get_logger(__name__)

# [[IMAGE PROMPT: <anything>]] - DOTALL so multi-line prompts match,
# IGNORECASE to be forgiving. Group 1 is the actual generation prompt.
_MARKER = re.compile(r"\[\[IMAGE PROMPT:\s*(.*?)\]\]", re.DOTALL | re.IGNORECASE)

# Fixed diagram-specialisation preamble so drafter prompts render as clean
# institutional diagrams regardless of phrasing.
_DIAGRAM_PREAMBLE = (
    "Flat vector diagram for a Horizon Europe grant proposal figure, EU "
    "institutional report style, white background, no photorealism, clean "
    "geometric boxes and arrows, short text labels only, high contrast, "
    "legible at small size. Render exactly and only this content, adding no "
    "extra boxes, labels, numbers or decoration:\n\n"
)


class DynamicFigureAgentInput(BaseModel):
    """Pydantic model defining the DynamicFigureAgentInput shape."""
    pass


class DynamicFigureAgentConfig(BaseModel):
    """Pydantic model defining the DynamicFigureAgentConfig shape.

    Attributes:
        content (str).
        image_model (str).
        size (str).
        quality (str).
        output_format (str).
        max_images (int).
        fail_open (bool).
    """
    content: str
    image_model: str = "gpt-image-2-2026-04-21"
    size: str = "auto"
    quality: str = "auto"
    output_format: str = "png"
    max_images: int = Field(default=10, ge=0, le=10)
    # If generation is disabled/unavailable, fall back to captioned output for
    # the illustrated variant too, so the pipeline never hard-fails on images.
    fail_open: bool = True


class GeneratedFigure(BaseModel):
    """Pydantic model defining the GeneratedFigure shape.

    Attributes:
        index (int).
        prompt (str).
        generated (bool).
        minio_key (str | None).
        byte_size (int | None).
        error (str | None).
    """
    index: int
    prompt: str
    generated: bool
    minio_key: str | None = None
    byte_size: int | None = None
    error: str | None = None


class DynamicFigureAgentOutput(BaseModel):
    """Pydantic model defining the DynamicFigureAgentOutput shape.

    Attributes:
        illustrated_content (str).
        captioned_content (str).
        figures (list[GeneratedFigure]).
        markers_found (int).
        images_generated (int).
    """
    illustrated_content: str = ""
    captioned_content: str = ""
    figures: list[GeneratedFigure] = Field(default_factory=list)
    markers_found: int = 0
    images_generated: int = 0


@NodeRegistry.register
class DynamicFigureAgent(NodeType):
    """Workflow node type implementing the DynamicFigureAgent capability.

    Attributes:
        input_schema (ClassVar[type[BaseModel]]).
        config_schema (ClassVar[type[BaseModel]]).
        output_schema (ClassVar[type[BaseModel]]).
    """
    type_name = "DynamicFigureAgent"
    description = (
        "Generate a diagram image for every [[IMAGE PROMPT: ...]] marker and "
        "embed it as a data URI the DOCX renderer can render; also emit a "
        "caption-only variant for text documents."
    )
    input_schema: ClassVar[type[BaseModel]] = DynamicFigureAgentInput
    config_schema: ClassVar[type[BaseModel]] = DynamicFigureAgentConfig
    output_schema: ClassVar[type[BaseModel]] = DynamicFigureAgentOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the result.

        Args:
            state (dict[str, Any]): Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = DynamicFigureAgentConfig(**resolved_config)
        content = cfg.content or ""

        markers = _MARKER.findall(content)
        markers = [m.strip() for m in markers]
        markers_found = len(markers)

        # captioned_content never depends on generation - build it always so
        # the text-only renderers always get clean captions.
        captioned_content = _sub_captioned(content)

        if markers_found == 0:
            return DynamicFigureAgentOutput(
                illustrated_content=content,
                captioned_content=content,
                figures=[],
                markers_found=0,
                images_generated=0,
            ).model_dump(mode="json")

        image_service = self.services.get("image_generation")
        store = self.services.get("object_store")

        # Cap how many we generate; extras degrade to captions in the
        # illustrated output too.
        to_generate = markers[: cfg.max_images]

        figures: list[GeneratedFigure] = []
        data_uris: dict[int, str] = {}

        if image_service is None or store is None:
            if not cfg.fail_open:
                raise RuntimeError(
                    "DynamicFigureAgent requires services "
                    "['image_generation', 'object_store']"
                )
            log.warning(
                "dynamic_figures.no_image_service",
                node_id=self.node_id,
                markers=markers_found,
            )
        else:
            async def _one(idx: int, prompt: str) -> GeneratedFigure:
                """Internal helper for the one step.

                Args:
                    idx (int): The idx.
                    prompt (str): Prompt text.

                Returns:
                    GeneratedFigure: The result.
                """
                full_prompt = _DIAGRAM_PREAMBLE + prompt
                try:
                    result = await image_service.generate(
                        prompt=full_prompt,
                        model=cfg.image_model,
                        size=cfg.size,
                        quality=cfg.quality,
                        output_format=cfg.output_format,
                    )
                    key = result.get("minio_key") if isinstance(result, dict) else None
                    raw = result.get("bytes") if isinstance(result, dict) else None
                    if raw is None and key is not None:
                        raw = await asyncio.to_thread(store.get_bytes, key)
                    if not raw:
                        return GeneratedFigure(
                            index=idx, prompt=prompt, generated=False,
                            error="no image bytes returned",
                        )
                    mime = f"image/{cfg.output_format}"
                    data_uris[idx] = (
                        f"data:{mime};base64,"
                        + base64.b64encode(raw).decode("ascii")
                    )
                    return GeneratedFigure(
                        index=idx, prompt=prompt, generated=True,
                        minio_key=key, byte_size=len(raw),
                    )
                except Exception as exc:  # fail-open per marker
                    return GeneratedFigure(
                        index=idx, prompt=prompt, generated=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )

            figures = await asyncio.gather(
                *(_one(i, p) for i, p in enumerate(to_generate))
            )

        # Build illustrated_content: replace markers in order. A marker with a
        # data URI becomes <figure><img data-uri>; anything else (over cap,
        # failed, or no service) degrades to the same caption the text docs use
        # so no raw marker ever survives.
        counter = {"n": 0}

        def _repl_illustrated(mobj: re.Match) -> str:
            """Internal helper for the repl illustrated step.

            Args:
                mobj (re.Match): The mobj.

            Returns:
                str: The illustrated.
            """
            i = counter["n"]
            counter["n"] += 1
            desc = mobj.group(1).strip()
            fig_no = i + 1
            uri = data_uris.get(i)
            if uri:
                alt = _escape(desc[:200])
                cap = _escape(desc[:160])
                return (
                    f'<figure><img src="{uri}" alt="{alt}"/>'
                    f"<figcaption>Figure {fig_no}: {cap}</figcaption></figure>"
                )
            return f"*Figure {fig_no}: {desc}*"

        illustrated_content = _MARKER.sub(_repl_illustrated, content)
        images_generated = sum(1 for f in figures if f.generated)

        log.info(
            "dynamic_figures.done",
            node_id=self.node_id,
            markers=markers_found,
            generated=images_generated,
            failed=markers_found - images_generated,
        )

        return DynamicFigureAgentOutput(
            illustrated_content=illustrated_content,
            captioned_content=captioned_content,
            figures=figures,
            markers_found=markers_found,
            images_generated=images_generated,
        ).model_dump(mode="json")


def _sub_captioned(content: str) -> str:
    """Internal helper for the sub captioned step.

    Args:
        content (str): Content value.

    Returns:
        str: The captioned.
    """
    counter = {"n": 0}

    def repl(mobj: re.Match) -> str:
        """Compute the repl.

        Args:
            mobj (re.Match): The mobj.

        Returns:
            str: The result.
        """
        counter["n"] += 1
        return f"*Figure {counter['n']}: {mobj.group(1).strip()}*"

    return _MARKER.sub(repl, content)


def _escape(text: str) -> str:
    """Internal helper for the escape step.

    Args:
        text (str): The text.

    Returns:
        str: The result.
    """
    return (
        text.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
