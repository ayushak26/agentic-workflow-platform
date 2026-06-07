"""
app/nodes/docx_renderer.py

DOCXProposalRenderer — pre-baked tool-node variant that renders a set of
Markdown sections into a styled Microsoft Word (.docx) document, stores it in
object storage (MinIO/S3), and returns the object key.

This is the .docx twin of PDFProposalRenderer. Same input contract (a
{heading: markdown} dict, a title, a client name, a template name), same
storage discipline (workflow state holds the MinIO key, NOT the file bytes —
bytes never enter LangGraph state).

Implementation library: python-docx  (same document-generation category as the
python-pptx / openpyxl / WeasyPrint tools already in the stack — a new VARIANT,
not a new tool category).

    pip install python-docx

YAML usage (drop-in replacement for the PDF renderer at the end of a workflow):

    - id: generate_docx
      type: DOCXProposalRenderer
      config:
        sections:
          "1. Excellence": "{{draft_excellence.parsed.markdown}}"
          "2. Impact": "{{draft_impact.parsed.markdown}}"
          "3. Implementation": "{{draft_implementation.parsed.markdown}}"
          "Annex - Digital Tool Architecture": "{{tool_architecture.parsed.markdown}}"
          "Submission Readiness": "{{compile_and_qa.parsed.readiness}}"
        template: corporate
        proposal_title: "CL6 Biomass Monitoring & Digital Tools Proposal (Part B) - DRAFT"
        client_name: "European Commission / Horizon Europe Cluster 6"

CRITICAL: remember to add `from app.nodes import docx_renderer` (or the project's
existing `import *`) in app/nodes/__init__.py so the @NodeRegistry.register
decorator fires at import time.
"""

from __future__ import annotations

import io
import re
import uuid
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor, Inches

# >>> VERIFY (1/3): match these imports to your actual base-class module paths.
# In the existing codebase the base NodeType and the registry live under
# app/nodes/base.py and app/nodes/registry.py (adjust if yours differ).
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


# --------------------------------------------------------------------------- #
# Template presets — the .docx analogue of the three WeasyPrint CSS templates
# (corporate / professional / warm). Each maps to fonts + an accent colour.
# Keep the three names identical to the PDF templates so a workflow can swap
# PDFProposalRenderer <-> DOCXProposalRenderer without changing `template`.
# --------------------------------------------------------------------------- #
_TEMPLATES: dict[str, dict[str, Any]] = {
    "corporate": {
        "body_font": "Calibri",
        "heading_font": "Calibri Light",
        "accent": RGBColor(0x0D, 0x1B, 0x2A),   # navy
        "accent_2": RGBColor(0xC8, 0xA9, 0x6E),  # gold
        "body_pt": 11,
    },
    "professional": {
        "body_font": "Georgia",
        "heading_font": "Georgia",
        "accent": RGBColor(0x1A, 0x3A, 0x5A),
        "accent_2": RGBColor(0x4A, 0x6A, 0x8A),
        "body_pt": 11,
    },
    "warm": {
        "body_font": "Cambria",
        "heading_font": "Cambria",
        "accent": RGBColor(0x7A, 0x3B, 0x2E),
        "accent_2": RGBColor(0xC0, 0x7A, 0x40),
        "body_pt": 11,
    },
}
_DEFAULT_TEMPLATE = "corporate"


@NodeRegistry.register("DOCXProposalRenderer")
class DOCXProposalRenderer(NodeType):
    """Render Markdown sections into a styled .docx and persist it to object storage."""
    type_name = "DOCXProposalRenderer" 
    # The Builder UI auto-generates a config form from config_schema; declaring
    # it richly is what makes this node configurable without code. (Same reason
    # the other tool nodes declare schemas.)
    input_schema: dict[str, Any] = {}  # sections come via resolved config, not edges
    output_schema: dict[str, Any] = {
        "object_key": "str",   # MinIO/S3 key — this is what flows into state
        "filename": "str",
        "byte_size": "int",
        "section_count": "int",
    }
    config_schema: dict[str, Any] = {
        "sections": {
            "type": "object",
            "description": "Ordered mapping of heading -> Markdown body. Rendered top-to-bottom.",
            "required": True,
        },
        "proposal_title": {"type": "string", "required": True},
        "client_name": {"type": "string", "required": False, "default": ""},
        "template": {
            "type": "string",
            "enum": list(_TEMPLATES.keys()),
            "default": _DEFAULT_TEMPLATE,
            "required": False,
        },
    }

    # >>> VERIFY (2/3): match this signature to your base NodeType.run(...).
    # The existing renderer reads already-placeholder-resolved `config`, pulls
    # `object_store` from the services dict, and uses run/session ids from the
    # execution context to build the key. If your base passes these differently
    # (e.g. a single `ctx` object, or `self.services`), adjust the unpacking
    # below — the body logic does not change.
    async def run(
        self,
        *,
        config: dict[str, Any],
        services: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]:
        sections: dict[str, str] = config["sections"]
        title: str = config["proposal_title"]
        client: str = config.get("client_name", "")
        template_name: str = config.get("template", _DEFAULT_TEMPLATE)
        tpl = _TEMPLATES.get(template_name, _TEMPLATES[_DEFAULT_TEMPLATE])

        # Build the document fully in memory; bytes never touch workflow state.
        doc = self._build_document(title, client, template_name, sections, tpl)
        buf = io.BytesIO()
        doc.save(buf)
        data = buf.getvalue()

        # >>> VERIFY (3/3): match key construction + object_store API to the PDF
        # renderer. Pattern below mirrors "state holds MinIO keys not bytes":
        # scope the key under the run so artifacts are isolated per run/session.
        run_id = getattr(context, "run_id", uuid.uuid4().hex)
        object_key = f"runs/{run_id}/proposal-{uuid.uuid4().hex[:8]}.docx"
        object_store = services["object_store"]
        await object_store.put(
            key=object_key,
            data=data,
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )

        return {
            "object_key": object_key,
            "filename": f"{_slug(title)}.docx",
            "byte_size": len(data),
            "section_count": len(sections),
        }

    # ----------------------------------------------------------------------- #
    # Document construction
    # ----------------------------------------------------------------------- #
    def _build_document(
        self,
        title: str,
        client: str,
        template_name: str,
        sections: dict[str, str],
        tpl: dict[str, Any],
    ) -> "Document":
        doc = Document()

        # A4 (Horizon Europe submissions are A4), 1-inch (2.5cm) margins.
        section = doc.sections[0]
        section.page_width = Inches(8.27)
        section.page_height = Inches(11.69)
        for m in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
            setattr(section, m, Inches(1))

        # Default body style.
        normal = doc.styles["Normal"]
        normal.font.name = tpl["body_font"]
        normal.font.size = Pt(tpl["body_pt"])

        self._add_title_block(doc, title, client, template_name, tpl)

        # Each section: a top-level heading, then its Markdown body.
        for heading, body in sections.items():
            h = doc.add_heading(heading, level=1)
            self._style_heading(h, tpl, top_level=True)
            self._render_markdown(doc, body or "", tpl)
            doc.add_paragraph()  # breathing room between sections

        return doc

    def _add_title_block(self, doc, title, client, template_name, tpl) -> None:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(title)
        run.bold = True
        run.font.size = Pt(22)
        run.font.name = tpl["heading_font"]
        run.font.color.rgb = tpl["accent"]

        if client:
            pc = doc.add_paragraph()
            pc.alignment = WD_ALIGN_PARAGRAPH.CENTER
            rc = pc.add_run(client)
            rc.font.size = Pt(13)
            rc.font.color.rgb = tpl["accent_2"]

        pn = doc.add_paragraph()
        pn.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rn = pn.add_run("DRAFT — illustrative; project-specific facts marked [TO CONFIRM]")
        rn.italic = True
        rn.font.size = Pt(9)

        doc.add_page_break()

    def _style_heading(self, paragraph, tpl, top_level: bool) -> None:
        for run in paragraph.runs:
            run.font.name = tpl["heading_font"]
            run.font.color.rgb = tpl["accent"] if top_level else tpl["accent_2"]

    # ----------------------------------------------------------------------- #
    # Minimal, robust Markdown -> docx renderer.
    # Supports the subset the LLM drafters actually emit:
    #   # / ## / ###  headings
    #   - or *        bullet lists
    #   1.            numbered lists
    #   **bold**, *italic*, `code`  inline spans
    #   | a | b |     pipe tables (with --- separator row)
    #   blank line    paragraph break
    # Anything else falls through as a plain paragraph.
    # ----------------------------------------------------------------------- #
    _BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
    _NUM_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
    _H_RE = re.compile(r"^(#{1,6})\s+(.*)$")
    _TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}.*$")

    def _render_markdown(self, doc, md: str, tpl) -> None:
        lines = md.replace("\r\n", "\n").split("\n")
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]

            # Blank line -> skip (paragraph breaks are implicit between blocks).
            if not line.strip():
                i += 1
                continue

            # Heading.
            m = self._H_RE.match(line)
            if m:
                level = min(len(m.group(1)), 4) + 1  # md # -> docx H2 (title is H1)
                h = doc.add_heading(m.group(2).strip(), level=min(level, 4))
                self._style_heading(h, tpl, top_level=False)
                i += 1
                continue

            # Table: a '|' line immediately followed by a --- separator line.
            if "|" in line and i + 1 < n and self._TABLE_SEP_RE.match(lines[i + 1]):
                i = self._render_table(doc, lines, i, tpl)
                continue

            # Bullet list block.
            if self._BULLET_RE.match(line):
                while i < n and self._BULLET_RE.match(lines[i]):
                    text = self._BULLET_RE.match(lines[i]).group(1)
                    p = doc.add_paragraph(style="List Bullet")
                    self._add_inline(p, text)
                    i += 1
                continue

            # Numbered list block.
            if self._NUM_RE.match(line):
                while i < n and self._NUM_RE.match(lines[i]):
                    text = self._NUM_RE.match(lines[i]).group(1)
                    p = doc.add_paragraph(style="List Number")
                    self._add_inline(p, text)
                    i += 1
                continue

            # Plain paragraph (greedy until blank/heading/list/table boundary).
            buf = [line]
            i += 1
            while i < n and lines[i].strip() and not (
                self._H_RE.match(lines[i])
                or self._BULLET_RE.match(lines[i])
                or self._NUM_RE.match(lines[i])
                or ("|" in lines[i] and i + 1 < n and self._TABLE_SEP_RE.match(lines[i + 1]))
            ):
                buf.append(lines[i])
                i += 1
            p = doc.add_paragraph()
            self._add_inline(p, " ".join(s.strip() for s in buf))

    def _render_table(self, doc, lines, start, tpl) -> int:
        """Render a pipe table starting at `start` (header row). Returns next index."""
        def cells(row: str) -> list[str]:
            row = row.strip()
            if row.startswith("|"):
                row = row[1:]
            if row.endswith("|"):
                row = row[:-1]
            return [c.strip() for c in row.split("|")]

        header = cells(lines[start])
        i = start + 2  # skip header + separator
        body_rows: list[list[str]] = []
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            body_rows.append(cells(lines[i]))
            i += 1

        cols = len(header)
        table = doc.add_table(rows=1, cols=cols)
        table.style = "Light Grid Accent 1"

        hdr = table.rows[0].cells
        for c, text in enumerate(header[:cols]):
            self._add_inline(hdr[c].paragraphs[0], text, bold=True)

        for r in body_rows:
            cells_out = table.add_row().cells
            for c in range(cols):
                text = r[c] if c < len(r) else ""
                self._add_inline(cells_out[c].paragraphs[0], text)

        doc.add_paragraph()
        return i

    # Inline spans: **bold**, *italic*, `code`. Order matters (bold before italic).
    _INLINE_RE = re.compile(r"(\*\*.+?\*\*|\*.+?\*|`.+?`)")

    def _add_inline(self, paragraph, text: str, bold: bool = False) -> None:
        for token in self._INLINE_RE.split(text):
            if not token:
                continue
            if token.startswith("**") and token.endswith("**"):
                r = paragraph.add_run(token[2:-2])
                r.bold = True
            elif token.startswith("`") and token.endswith("`"):
                r = paragraph.add_run(token[1:-1])
                r.font.name = "Consolas"
            elif token.startswith("*") and token.endswith("*"):
                r = paragraph.add_run(token[1:-1])
                r.italic = True
            else:
                r = paragraph.add_run(token)
            if bold:
                r.bold = True


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:60] or "proposal"