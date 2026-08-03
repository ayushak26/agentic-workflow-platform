"""Regression coverage for source_errors_from_payload (app/evidence/retrieval.py).

Root cause this closes: paper-search-mcp's search_papers can succeed overall
(some sources return papers) while individual sources fail internally (auth
rejection, rate limit) -- reported via a payload-level `errors: {source:
message}` map that app/evidence/retrieval.py's papers_from_payload() never
read. A Semantic Scholar rate-limit looked identical to "zero results, no
papers on this topic" with zero visible cause. Confirmed against a real
paper-search-mcp invocation during investigation: the actual response shape
is {"source_results": {source: count}, "errors": {source: message}, "papers": [...]}.
"""
from __future__ import annotations

import json

from app.evidence.retrieval import papers_from_payload, source_errors_from_payload

REAL_SHAPE_PAYLOAD = {
    "query": "machine learning",
    "sources_used": ["semantic", "zenodo", "hal"],
    "source_results": {"semantic": 5, "zenodo": 0, "hal": 0},
    "errors": {
        "zenodo": "'str' object has no attribute 'isoformat'",
        "hal": "'str' object has no attribute 'isoformat'",
    },
    "papers": [{"title": "A paper", "source": "semantic"}],
}


def test_extracts_per_source_errors_from_dict_payload():
    errors = source_errors_from_payload(REAL_SHAPE_PAYLOAD)
    assert errors == {
        "zenodo": "'str' object has no attribute 'isoformat'",
        "hal": "'str' object has no attribute 'isoformat'",
    }


def test_papers_still_extracted_alongside_errors():
    papers = papers_from_payload(REAL_SHAPE_PAYLOAD)
    assert len(papers) == 1
    assert papers[0]["title"] == "A paper"


def test_no_errors_key_returns_empty_dict():
    payload = {"papers": [{"title": "x"}]}
    assert source_errors_from_payload(payload) == {}


def test_list_payload_has_no_errors():
    assert source_errors_from_payload([{"title": "x"}]) == {}


def test_non_dict_non_list_payload_has_no_errors():
    assert source_errors_from_payload("just a string") == {}
    assert source_errors_from_payload(None) == {}
    assert source_errors_from_payload(42) == {}


def test_handles_double_json_encoded_payload():
    """MCP tool results sometimes arrive as a JSON string, occasionally
    double-encoded -- parse_mcp_payload (shared with papers_from_payload)
    already handles this; confirm source_errors_from_payload does too."""
    encoded_once = json.dumps(REAL_SHAPE_PAYLOAD)
    assert source_errors_from_payload(encoded_once) == {
        "zenodo": "'str' object has no attribute 'isoformat'",
        "hal": "'str' object has no attribute 'isoformat'",
    }


def test_errors_with_non_string_values_are_stringified():
    payload = {"errors": {"weird_source": {"code": 500, "detail": "oops"}}}
    errors = source_errors_from_payload(payload)
    assert errors["weird_source"] == "{'code': 500, 'detail': 'oops'}"


def test_errors_key_present_but_wrong_type_is_ignored():
    payload = {"errors": ["not", "a", "dict"]}
    assert source_errors_from_payload(payload) == {}
