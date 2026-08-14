"""Vision-model description of PDF pages carrying figures, charts and tables.

Text extraction alone loses whatever a PDF says visually — a pump curve, a
compatibility matrix rendered as an image, an exploded parts diagram. This
module renders those pages and asks a vision model to transcribe them, so the
content becomes chunkable, embeddable and retrievable like any other text.

It is opt-in: a Parser Profile selects the ``vision_augmented`` strategy, and
the page budget bounds the cost. Failure is never fatal — a page that cannot be
described falls back to its extracted text.
"""
from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings, settings as default_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

DEFAULT_PROMPT = (
    "Transcribe this page for a search index. Reproduce tables as markdown "
    "tables with every cell value. Describe each figure, chart, diagram or "
    "photograph precisely: what it shows, axis labels, units, series names, "
    "and any numbers or part labels printed on it. Do not summarise, "
    "speculate, or add commentary. If the page has no figures or tables, "
    "reply with exactly NO_VISUAL_CONTENT."
)

NO_VISUAL = "NO_VISUAL_CONTENT"


@dataclass(frozen=True)
class PageDescription:
    page_index: int
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class PdfPageVisionDescriber:
    """Render PDF pages and describe their visual content with a vision model.

    Talks to any OpenAI-compatible chat endpoint that accepts image parts —
    OpenRouter by default, or the configured Kimi/Moonshot endpoint.
    """

    def __init__(self, app_settings: Settings | None = None, *, client: Any | None = None):
        self.settings = app_settings or default_settings
        self._client = client

    # ---- capability ----------------------------------------------------

    @property
    def provider(self) -> str:
        return self.settings.ingestion_vision_provider

    @property
    def model(self) -> str:
        configured = self.settings.ingestion_vision_model.strip()
        if configured:
            return configured
        if self.provider == "kimi":
            return self.settings.kimi_vision_model
        return "google/gemini-2.5-flash"

    def available(self) -> bool:
        if self._client is not None:
            return True
        if not _can_render():
            return False
        if self.provider == "kimi":
            return bool(self.settings.moonshot_api_key.strip())
        return bool(self.settings.openrouter_api_key.strip())

    def unavailable_reason(self) -> str:
        if not _can_render():
            return "pypdfium2 is not installed, so PDF pages cannot be rendered"
        if self.provider == "kimi" and not self.settings.moonshot_api_key.strip():
            return "LOCAL_KIMI_API_KEY is not configured"
        if self.provider != "kimi" and not self.settings.openrouter_api_key.strip():
            return "OPENROUTER_API_KEY is not configured"
        return ""

    # ---- description ---------------------------------------------------

    async def describe_pages(
        self,
        path: Path,
        page_indexes: list[int],
        *,
        prompt: str | None = None,
    ) -> dict[int, PageDescription]:
        """Describe the given pages. Pages that fail are simply omitted."""
        if not page_indexes:
            return {}
        budget = max(0, int(self.settings.ingestion_vision_max_pages))
        selected = sorted(page_indexes)[:budget]
        if not selected:
            return {}

        images = await asyncio.to_thread(_render_pages, path, selected, self.settings.ingestion_vision_scale)
        semaphore = asyncio.Semaphore(max(1, int(self.settings.ingestion_vision_concurrency)))

        async def one(index: int, png: bytes) -> PageDescription | None:
            async with semaphore:
                try:
                    return await self._describe(index, png, prompt or DEFAULT_PROMPT)
                except Exception as exc:  # noqa: BLE001 - a page must never fail ingestion
                    log.warning(
                        "ingestion.vision_page_failed",
                        path=str(path),
                        page_index=index,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    return None

        results = await asyncio.gather(*(one(i, png) for i, png in images.items()))
        described = {
            item.page_index: item
            for item in results
            if item is not None and item.text.strip() and NO_VISUAL not in item.text
        }
        log.info(
            "ingestion.vision_done",
            path=str(path),
            requested=len(selected),
            described=len(described),
            model=self.model,
            provider=self.provider,
        )
        return described

    async def _describe(self, index: int, png: bytes, prompt: str) -> PageDescription:
        client = self._client or self._build_client()
        data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        completion = await client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            max_completion_tokens=int(self.settings.ingestion_vision_max_output_tokens),
        )
        message = completion.choices[0].message
        usage = getattr(completion, "usage", None)
        return PageDescription(
            page_index=index,
            text=str(message.content or "").strip(),
            model=self.model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )

    def _build_client(self) -> Any:
        from openai import AsyncOpenAI

        reason = self.unavailable_reason()
        if reason:
            raise RuntimeError(f"vision-augmented parsing is unavailable: {reason}")
        if self.provider == "kimi":
            return AsyncOpenAI(
                api_key=self.settings.moonshot_api_key.strip(),
                base_url=self.settings.kimi_api_base_url,
                timeout=self.settings.llm_request_timeout_seconds,
            )
        return AsyncOpenAI(
            api_key=self.settings.openrouter_api_key.strip(),
            base_url=self.settings.openrouter_base_url,
            timeout=self.settings.llm_request_timeout_seconds,
        )


# ---------- Rendering ---------------------------------------------------------


def _can_render() -> bool:
    try:
        import pypdfium2  # noqa: F401
    except Exception:  # noqa: BLE001 - optional dependency
        return False
    return True


def _render_pages(path: Path, indexes: list[int], scale: float) -> dict[int, bytes]:
    """Render the given 0-based page indexes to PNG bytes."""
    import pypdfium2 as pdfium

    out: dict[int, bytes] = {}
    document = pdfium.PdfDocument(str(path))
    try:
        count = len(document)
        for index in indexes:
            if index < 0 or index >= count:
                continue
            bitmap = document[index].render(scale=max(0.5, float(scale)))
            image = bitmap.to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            out[index] = buffer.getvalue()
    finally:
        document.close()
    return out


def pages_with_visual_content(path: Path, *, min_images: int = 1) -> list[int]:
    """0-based indexes of PDF pages that carry figures, images or table rules.

    Used to spend the vision budget only where text extraction actually loses
    something, rather than on every page of a prose document.
    """
    import pdfplumber

    interesting: list[int] = []
    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages):
            images = len(page.images or [])
            curves = len(getattr(page, "curves", []) or [])
            rects = len(getattr(page, "rects", []) or [])
            text = (page.extract_text() or "").strip()
            # A page earns vision when it embeds images, draws a lot of vector
            # graphics (charts), or is visually dense but textually empty.
            if images >= min_images or curves > 12 or rects > 24 or not text:
                interesting.append(index)
    return interesting
