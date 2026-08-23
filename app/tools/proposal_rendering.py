"""Secure, proposal-aware HTML/Markdown to PDF rendering."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import bleach
import pdfplumber
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt
from markupsafe import Markup


_TEMPLATES_DIR = Path(__file__).parent / "templates"
_JINJA = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)
_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code",
    "col", "colgroup", "dd", "del", "details", "div", "dl", "dt", "em",
    "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
    "i", "img", "li", "mark", "ol", "p", "pre", "s", "small", "span",
    "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "u", "ul",
}
_ALLOWED_ATTRIBUTES = {
    "*": [
        "class",
        "id",
        "title",
        "data-image-prompt",
        "data-citation-id",
        "data-source-id",
    ],
    "a": ["href", "title"],
    "img": ["src", "alt", "title", "width", "height"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
    "col": ["span", "width"],
}
_INPUT_MARKER = re.compile(r"\[INPUT NEEDED:\s*([^\]]+)\]", re.IGNORECASE)
_IMAGE_PROMPT_MARKER = re.compile(
    r"\[\[IMAGE PROMPT:\s*([^\]]+)\]\]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProposalRenderResult:
    """Provides the ProposalRenderResult behaviour.

    Attributes:
        html (str).
        pdf (bytes).
        page_count (int).
        html_sha256 (str).
        pdf_sha256 (str).
        table_of_contents (list[dict[str, Any]]).
        warnings (list[str]).
    """
    html: str
    pdf: bytes
    page_count: int
    html_sha256: str
    pdf_sha256: str
    table_of_contents: list[dict[str, Any]]
    warnings: list[str]


def _markdown() -> MarkdownIt:
    """Internal helper for the markdown step.

    Returns:
        MarkdownIt: The result.
    """
    renderer = MarkdownIt(
        "commonmark",
        {
            "html": False,
            "linkify": False,
            "typographer": False,
        },
    )
    renderer.enable("table")
    renderer.enable("strikethrough")
    return renderer


def _slug(value: str) -> str:
    """Internal helper for the slug step.

    Args:
        value (str): Value to process.

    Returns:
        str: The result.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "section"


def _unique_id(base: str, used: set[str]) -> str:
    """Internal helper for the unique id step.

    Args:
        base (str): The base.
        used (set[str]): The used.

    Returns:
        str: The id.
    """
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _sanitise_fragment(value: str, content_format: str) -> str:
    """Internal helper for the sanitise fragment step.

    Args:
        value (str): Value to process.
        content_format (str): The content format.

    Returns:
        str: The fragment.
    """
    if content_format == "markdown":
        value = _markdown().render(value)
    elif content_format != "html":
        raise ValueError("content_format must be 'markdown' or 'html'")

    value = _IMAGE_PROMPT_MARKER.sub(
        lambda match: (
            '<figure class="image-placeholder" data-image-prompt="'
            + bleach.clean(match.group(1), tags=set(), strip=True)
            + '"><figcaption>Image placeholder - prompt: '
            + bleach.clean(match.group(1), tags=set(), strip=True)
            + "</figcaption></figure>"
        ),
        value,
    )
    cleaned = bleach.clean(
        value,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols={"https", "http", "mailto", "data"},
        strip=True,
        strip_comments=True,
    )
    soup = BeautifulSoup(cleaned, "html.parser")

    # Remote image fetching is disabled. Keep the requested visual as a clear
    # prompt placeholder rather than producing a broken or untraceable figure.
    for image in list(soup.find_all("img")):
        source = str(image.get("src") or "")
        if source.startswith("data:image/"):
            continue
        prompt = image.get("alt") or image.get("title") or "Visual to be supplied"
        placeholder = soup.new_tag("figure")
        placeholder["class"] = ["image-placeholder"]
        placeholder["data-image-prompt"] = prompt
        caption = soup.new_tag("figcaption")
        caption.string = f"Image placeholder - prompt: {prompt}"
        placeholder.append(caption)
        image.replace_with(placeholder)

    for item in soup.find_all(attrs={"data-image-prompt": True}):
        classes = list(item.get("class") or [])
        if "image-placeholder" not in classes:
            classes.append("image-placeholder")
        item["class"] = classes
        if not item.get_text(" ", strip=True):
            item.string = (
                "Image placeholder - prompt: "
                + str(item.get("data-image-prompt"))
            )

    used: set[str] = set()
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        existing = str(heading.get("id") or "").strip()
        heading["id"] = _unique_id(
            _slug(existing or heading.get_text(" ", strip=True)),
            used,
        )

    # Missing proposal inputs stay visible in the generated PDF.
    marker_html = _INPUT_MARKER.sub(
        lambda match: (
            '<span class="input-needed">[INPUT NEEDED: '
            + bleach.clean(match.group(1), tags=set(), strip=True)
            + "]</span>"
        ),
        str(soup),
    )
    return marker_html


def _toc(fragment: str) -> list[dict[str, Any]]:
    """Internal helper for the toc step.

    Args:
        fragment (str): The fragment.

    Returns:
        list[dict[str, Any]]: The result.
    """
    soup = BeautifulSoup(fragment, "html.parser")
    entries: list[dict[str, Any]] = []
    for heading in soup.find_all(["h1", "h2", "h3"]):
        entries.append(
            {
                "level": int(heading.name[1]),
                "id": str(heading.get("id")),
                "title": heading.get_text(" ", strip=True),
            }
        )
    return entries


def _safe_url_fetcher(url: str, *args: Any, **kwargs: Any):
    """Allow embedded images and renderer-owned local assets only."""

    from weasyprint import default_url_fetcher

    parsed = urlparse(url)
    if parsed.scheme == "data":
        return default_url_fetcher(url, *args, **kwargs)
    if parsed.scheme in {"", "file"}:
        path = Path(parsed.path).resolve()
        root = _TEMPLATES_DIR.resolve()
        if path == root or root in path.parents:
            return default_url_fetcher(url, *args, **kwargs)
    raise ValueError(f"External resource loading is disabled: {parsed.scheme or 'path'}")


def render_horizon_proposal(
    *,
    content: str,
    content_format: Literal["markdown", "html"],
    metadata: dict[str, Any],
    citation_registry: list[dict[str, Any]] | None = None,
    evidence_qa: dict[str, Any] | None = None,
    evidence_blockers: list[str] | None = None,
    include_toc: bool = True,
    include_bibliography: bool = True,
    include_evidence_annex: bool = False,
    page_limit: int | None = 45,
) -> ProposalRenderResult:
    """Render a Horizon Part B proposal and return immutable output metadata."""

    from weasyprint import CSS, HTML

    citation_registry = citation_registry or []
    evidence_qa = evidence_qa or {}
    evidence_blockers = evidence_blockers or []
    fragment = _sanitise_fragment(content, content_format)
    toc = _toc(fragment) if include_toc else []
    proposal_metadata = {
        "proposal_title": "Untitled proposal",
        "acronym": "",
        "call_id": "",
        "topic_title": "",
        "action_type": "",
        "consortium_name": "",
        "coordinator": "",
        "version": "Draft",
        "document_date": date.today().isoformat(),
        "part_label": "Part B",
        "confidentiality": "Confidential",
        **metadata,
    }
    template = _JINJA.get_template("horizon_part_b.html.j2")
    html = template.render(
        metadata=proposal_metadata,
        content=Markup(fragment),
        toc=toc,
        citation_registry=citation_registry,
        evidence_qa=evidence_qa,
        evidence_blockers=evidence_blockers,
        include_toc=include_toc,
        include_bibliography=include_bibliography,
        include_evidence_annex=include_evidence_annex,
    )
    css_path = _TEMPLATES_DIR / "horizon_part_b.css"
    pdf = HTML(
        string=html,
        base_url=str(_TEMPLATES_DIR),
        url_fetcher=_safe_url_fetcher,
    ).write_pdf(stylesheets=[CSS(filename=str(css_path))])
    with pdfplumber.open(BytesIO(pdf)) as document:
        page_count = len(document.pages)

    warnings: list[str] = []
    if page_limit is not None and page_count > page_limit:
        warnings.append(
            f"Rendered proposal is {page_count} pages; configured limit is "
            f"{page_limit} pages."
        )
    input_markers = len(_INPUT_MARKER.findall(content))
    if input_markers:
        warnings.append(
            f"{input_markers} visible INPUT NEEDED marker(s) remain."
        )
    if evidence_blockers:
        warnings.append(
            f"{len(evidence_blockers)} evidence blocker(s) remain."
        )
    return ProposalRenderResult(
        html=html,
        pdf=pdf,
        page_count=page_count,
        html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        pdf_sha256=hashlib.sha256(pdf).hexdigest(),
        table_of_contents=toc,
        warnings=warnings,
    )
