#!/usr/bin/env python3
"""Render the checked-in Horizon proposal DOCX sample for visual QA."""
from __future__ import annotations

import json
from pathlib import Path

from app.tools.docx_proposal_rendering import render_horizon_proposal_docx


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples" / "horizon_proposal_demo.md"
OUTPUT_DIR = ROOT / "output" / "docx"


def main() -> None:
    rendered = render_horizon_proposal_docx(
        content=SOURCE.read_text(encoding="utf-8"),
        content_format="markdown",
        metadata={
            "proposal_title": (
                "AGRO-THRIVE: Balancing food security, bioeconomy, "
                "climate and biodiversity"
            ),
            "acronym": "AGRO-THRIVE",
            "call_id": "HORIZON-CL6-2026-01-CIRCBIO-09",
            "action_type": "Research and Innovation Action (RIA)",
            "consortium_name": "AGRO-THRIVE Consortium",
            "version": "DOCX renderer validation sample",
            "part_label": "Part B",
        },
        citation_registry=[
            {
                "citation_id": "CIT-0001",
                "source_id": "SRC-DEMO-1",
                "formatted_citation": (
                    "Example Researcher et al. Verified source one. 2025."
                ),
            },
            {
                "citation_id": "CIT-0002",
                "source_id": "SRC-DEMO-2",
                "formatted_citation": (
                    "Example Researcher et al. Verified source two. 2024."
                ),
            },
        ],
        evidence_qa={
            "policy_version": "eurskem-evidence-v2.0",
            "claims_examined": 2,
            "verified_claims": 2,
            "exact_locator_rate": 1.0,
        },
        evidence_blockers=[],
        include_toc=True,
        include_bibliography=True,
        include_evidence_annex=True,
        page_limit=45,
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    docx_path = (
        OUTPUT_DIR
        / "AGRO_THRIVE_Horizon_Proposal_DOCX_Renderer_Sample.docx"
    )
    html_path = (
        OUTPUT_DIR
        / "AGRO_THRIVE_Horizon_Proposal_DOCX_Renderer_Source.html"
    )
    report_path = (
        OUTPUT_DIR
        / "AGRO_THRIVE_Horizon_Proposal_DOCX_Renderer_Sample.json"
    )
    docx_path.write_bytes(rendered.docx)
    html_path.write_text(rendered.source_html, encoding="utf-8")
    report_path.write_text(
        json.dumps(
            {
                "estimated_page_count": rendered.estimated_page_count,
                "page_count_basis": rendered.page_count_basis,
                "docx_sha256": rendered.docx_sha256,
                "source_html_sha256": rendered.source_html_sha256,
                "table_of_contents": rendered.table_of_contents,
                "warnings": rendered.warnings,
                "embedded_image_count": rendered.embedded_image_count,
                "image_placeholder_count": (
                    rendered.image_placeholder_count
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(docx_path)
    print(html_path)
    print(report_path)


if __name__ == "__main__":
    main()
