from __future__ import annotations

from app.nodes.html_proposal_renderer import HorizonHTMLProposalRenderer
from app.tools.proposal_rendering import render_horizon_proposal


class StubObjectStore:
    def __init__(self):
        self.blobs = {}

    def put_bytes(self, data, key, content_type=None):
        self.blobs[key] = (data, content_type)


def test_renderer_builds_toc_tables_placeholders_and_blocks_scripts():
    result = render_horizon_proposal(
        content="""
<h1>1. Excellence</h1>
<h2>1.1 Objectives</h2>
<p>The target remains <strong>[INPUT NEEDED: validated baseline]</strong>.</p>
<table>
  <thead><tr><th>KPI</th><th>Target</th></tr></thead>
  <tbody><tr><td>Accuracy</td><td>90%</td></tr></tbody>
</table>
<script>alert('x')</script>
<img src="https://example.com/untrusted.png" alt="Map of pilot regions">
<h1>2. Impact</h1>
<p>Impact pathway text.</p>
""",
        content_format="html",
        metadata={
            "proposal_title": "AGRO-THRIVE Proposal",
            "acronym": "AGRO-THRIVE",
            "call_id": "HORIZON-CL6-2026-01-CIRCBIO-09",
        },
        page_limit=45,
    )

    assert result.pdf.startswith(b"%PDF-")
    assert result.page_count >= 3
    assert [item["title"] for item in result.table_of_contents] == [
        "1. Excellence",
        "1.1 Objectives",
        "2. Impact",
    ]
    assert "<script" not in result.html
    assert "https://example.com/untrusted.png" not in result.html
    assert "image-placeholder" in result.html
    assert any("INPUT NEEDED" in item for item in result.warnings)


async def test_horizon_renderer_node_stores_html_and_pdf():
    store = StubObjectStore()
    node = HorizonHTMLProposalRenderer(
        "render",
        {
            "content": (
                "# 1. Excellence\n\n"
                "Grounded proposal text [1].\n\n"
                "# 2. Impact\n\n"
                "A credible impact pathway."
            ),
            "content_format": "markdown",
            "metadata": {
                "proposal_title": "Proposal",
                "acronym": "TEST",
            },
            "citation_registry": [
                {
                    "citation_id": "CIT-0001",
                    "source_id": "SRC-1",
                    "formatted_citation": "Researcher. Verified source. 2025.",
                }
            ],
            "evidence_qa": {
                "claims_examined": 1,
                "verified_claims": 1,
            },
            "evidence_blockers": [],
            "include_evidence_annex": True,
        },
        services={"object_store": store},
    )
    result = await node.run(
        state={"inputs": {"SYSTEM.run_id": "run-123"}},
        resolved_config=node.config.model_dump(),
    )

    assert result["minio_key"] == "workflows/run-123/proposal.pdf"
    assert result["html_key"] == "workflows/run-123/proposal.html"
    assert result["page_count"] >= 4
    assert result["submission_ready"] is True
    assert store.blobs[result["pdf_key"]][0].startswith(b"%PDF-")
    assert b"Evidence integrity annex" in store.blobs[result["html_key"]][0]
