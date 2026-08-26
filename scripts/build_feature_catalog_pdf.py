#!/usr/bin/env python3
"""Render docs/EURSKEM_AI_FEATURE_CATALOG.md as a standalone PDF."""
from __future__ import annotations

import re
from pathlib import Path

from markdown_it import MarkdownIt
from weasyprint import CSS, HTML

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "EURSKEM_AI_FEATURE_CATALOG.md"
OUTPUT = ROOT / "docs" / "EURSKEM_AI_FEATURE_CATALOG.pdf"

STYLE = r"""
@page { size:A4; margin:17mm 13mm 19mm;
  @bottom-center { content:counter(page) " / " counter(pages); font:8pt Arial; color:#7b8494 }
  @bottom-right { content:"Eurskem AI Feature Catalog"; font:7pt Arial; color:#7b8494 }}
@page cover { margin:0;
  @bottom-center { content:none }
  @bottom-right { content:none }}
* { box-sizing:border-box }
body { font-family:"Helvetica Neue",Arial,sans-serif; font-size:9.2pt; line-height:1.43; color:#202735 }
.cover { page:cover; width:210mm; height:297mm; padding:40mm 24mm; color:white;
  background:linear-gradient(145deg,#081325,#12294b 56%,#075b65) }
.cover .series { text-transform:uppercase; letter-spacing:.24em; color:#78dfca; font-size:9pt }
.cover h1 { color:white; border:0; font-size:36pt; line-height:1.08; margin:12mm 0 7mm;
  padding:0; break-before:auto; max-width:160mm }
.cover .sub { color:#d3deec; font-size:14pt; max-width:150mm }
.cover .meta { color:#a8bad0; margin-top:45mm; line-height:2 }
h1 { color:#0b5260; font-size:18pt; border-bottom:2px solid #18a28b; padding-bottom:2mm;
  margin:8mm 0 4mm; break-before:page }
h2 { color:#142e50; font-size:13.5pt; margin:7mm 0 2mm; break-after:avoid }
h3 { color:#0b5260; font-size:11.5pt; margin:5mm 0 1.5mm; break-after:avoid }
p { margin:0 0 3mm } ul,ol { margin:1mm 0 4mm 6mm; padding-left:4mm }
code { font-family:Menlo,Consolas,monospace; font-size:7.4pt; color:#075b65;
  background:#eef3f6; padding:.3mm .8mm; border-radius:2px; overflow-wrap:anywhere }
table { width:100%; border-collapse:collapse; table-layout:fixed; margin:2.5mm 0 5mm;
  font-size:7.2pt; break-inside:auto }
thead { display:table-header-group } tr { break-inside:avoid }
th { background:#142e50; color:white; text-align:left; padding:1.6mm 1.7mm; overflow-wrap:anywhere }
td { border-bottom:.3pt solid #d9e0e8; padding:1.3mm 1.7mm; vertical-align:top; overflow-wrap:anywhere }
tr:nth-child(even) td { background:#f6f8fa }
table th:first-child,table td:first-child { width:13% }
table th:nth-child(2),table td:nth-child(2) { width:33% }
table th:nth-child(3),table td:nth-child(3) { width:16% }
table th:nth-child(4),table td:nth-child(4) { width:38% }
.toc { break-after:page; background:#f3f7f9; border-left:4px solid #18a28b;
  padding:4mm 6mm; margin:8mm 0 }
.toc h2 { margin-top:0 }.toc ol { columns:2; column-gap:8mm }
blockquote { border-left:4px solid #18a28b; background:#eef8f6; padding:3mm 4mm; margin:4mm 0 }
hr { border:0; border-top:.5pt solid #ccd6df; margin:5mm 0 } a { color:#075b65 }
"""


def _toc(markdown: str) -> str:
    items: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("# ") and not line.startswith("# Eurskem"):
            title = line[2:].strip()
            items.append(f"<li>{title}</li>")
    return '<section class="toc"><h2>Table of Contents</h2><ol>' + "".join(items) + "</ol></section>"


def main() -> int:
    markdown = SOURCE.read_text(encoding="utf-8")
    rendered = MarkdownIt("commonmark", {"html": True, "typographer": True}).render(markdown)
    rendered = re.sub(r"^<h1>.*?</h1>\s*", "", rendered, count=1, flags=re.DOTALL)
    cover = (
        '<section class="cover"><div class="series">Engineering Feature Reference</div>'
        '<h1>Eurskem AI<br>Complete Feature Catalog</h1>'
        '<div class="sub">Product surfaces, workflow capabilities, AI and RAG, '
        'integrations, security, operations, tests, and registered node types.</div>'
        '<div class="meta">Agentic Workflow Platform<br>Catalog date: 24 August 2026<br>'
        'Derived from the current repository</div></section>'
    )
    document = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Eurskem AI — Complete Feature Catalog</title></head><body>"
        + cover
        + _toc(markdown)
        + rendered
        + "</body></html>"
    )
    HTML(string=document, base_url=str(ROOT)).write_pdf(OUTPUT, stylesheets=[CSS(string=STYLE)])
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())