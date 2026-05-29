"""PDF read + render utilities."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
from typing import Any

import pdfplumber
from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
)


def extract_text_from_pdf(file_bytes: bytes) -> list[dict[str, Any]]:
    """Return a list of {page_num, text} for each page."""
    pages: list[dict[str, Any]] = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pages.append({
                "page_num": i,
                "text": page.extract_text() or "",
            })
    return pages


def render_proposal_pdf(
    sections: dict[str, str],
    template: str,
    client_name: str,
    proposal_title: str,
) -> bytes:
    """Render an HTML proposal, then convert to PDF bytes via WeasyPrint."""
    # Import here so module import doesn't fail on systems without WeasyPrint
    from weasyprint import HTML, CSS

    available_templates = {"corporate", "professional", "warm"}
    if template not in available_templates:
        raise ValueError(
            f"Unknown template {template!r}. Available: {sorted(available_templates)}"
        )

    html_template = _jinja.get_template("proposal.html.j2")
    html_text = html_template.render(
        sections=sections,
        client_name=client_name,
        proposal_title=proposal_title,
        template=template,
    )

    css_path = _TEMPLATES_DIR / f"{template}.css"
    pdf_bytes = HTML(string=html_text, base_url=str(_TEMPLATES_DIR)).write_pdf(
        stylesheets=[CSS(filename=str(css_path))],
    )
    return pdf_bytes