"""Regression coverage for the HorizonHTMLProposalRendererConfig(content={})
bug class: a template reference resolving to the wrong shape (dict/list/None
instead of a string) must fail loudly, specifically at the offending field,
for every document renderer node — HTML, DOCX (both variants), PDF, PPTX,
and Excel.

The root cause (a bare {{node.parsed}} reference on a TransformAgent with no
output_schema, always {}) is now caught earlier and for free by a new
preflight check — see tests/test_preflight_static_output_values.py. This
file protects the Pydantic layer itself: even if some other path resolves
the wrong shape into these configs (a future node type, a bug in a
different template source), construction must still fail clearly.
"""
from __future__ import annotations

from app.nodes.docx_renderer import DOCXRenderConfig
from app.nodes.excel_tool import ExcelConfig
from app.nodes.horizon_docx_renderer import HorizonDOCXProposalRendererConfig
from app.nodes.html_proposal_renderer import HorizonHTMLProposalRendererConfig
from app.nodes.pdf_tool import PDFRenderConfig
from app.nodes.powerpoint_tool import PPTConfig
from tests.helpers.pydantic_validation import (
    assert_accepts,
    assert_field_rejects_non_string,
)


def test_horizon_html_renderer_content_must_be_a_string():
    assert_field_rejects_non_string(
        HorizonHTMLProposalRendererConfig,
        "content",
        valid_kwargs={"content": "# Proposal\n\nBody text."},
    )


def test_horizon_docx_renderer_content_must_be_a_string():
    assert_field_rejects_non_string(
        HorizonDOCXProposalRendererConfig,
        "content",
        valid_kwargs={"content": "# Proposal\n\nBody text."},
    )


def test_docx_renderer_proposal_title_and_client_name_must_be_strings():
    valid = {
        "sections": {"intro": "Body text."},
        "proposal_title": "My Proposal",
        "client_name": "Acme Corp",
    }
    assert_field_rejects_non_string(
        DOCXRenderConfig, "proposal_title", valid_kwargs=valid
    )
    assert_field_rejects_non_string(
        DOCXRenderConfig, "client_name", valid_kwargs=valid
    )


def test_pdf_renderer_proposal_title_and_client_name_must_be_strings():
    valid = {
        "sections": {"intro": "Body text."},
        "proposal_title": "My Proposal",
        "client_name": "Acme Corp",
    }
    assert_field_rejects_non_string(
        PDFRenderConfig, "proposal_title", valid_kwargs=valid
    )
    assert_field_rejects_non_string(
        PDFRenderConfig, "client_name", valid_kwargs=valid
    )


def test_ppt_renderer_proposal_title_and_client_name_must_be_strings():
    valid = {
        "sections": {"intro": "Body text."},
        "proposal_title": "My Proposal",
        "client_name": "Acme Corp",
    }
    assert_field_rejects_non_string(
        PPTConfig, "proposal_title", valid_kwargs=valid
    )
    assert_field_rejects_non_string(
        PPTConfig, "client_name", valid_kwargs=valid
    )


def test_excel_extractor_minio_key_must_be_a_string():
    assert_field_rejects_non_string(
        ExcelConfig, "minio_key", valid_kwargs={"minio_key": "evidence/foo.xlsx"}
    )


def test_renderer_configs_accept_str_or_dict_for_union_typed_fields():
    """metadata/citation_registry/evidence_qa/evidence_blockers are
    deliberately typed as `str | dict`/`str | list` unions -- unlike
    `content`, a dict/list here is valid, not the bug this file guards
    against. Confirms the distinction is intentional, not an oversight."""
    assert_accepts(
        HorizonHTMLProposalRendererConfig,
        content="Body text.",
        metadata={"title": "My Proposal"},
        citation_registry=[{"id": "1"}],
        evidence_qa={"status": "ok"},
        evidence_blockers=["missing figure 2"],
    )
    assert_accepts(
        HorizonDOCXProposalRendererConfig,
        content="Body text.",
        metadata={"title": "My Proposal"},
        citation_registry=[{"id": "1"}],
        evidence_qa={"status": "ok"},
        evidence_blockers=["missing figure 2"],
    )
