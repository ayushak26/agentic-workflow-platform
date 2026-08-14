"""Filter-contract regressions for the retrieval API.

Both cases below failed silently rather than loudly, which is what made them
dangerous: the caller believed a filter was applied and got unfiltered results.
"""
from __future__ import annotations

import pytest

from app.api.retrieval import RetrievalSearchRequest
from app.retrieval.filters import (
    MetadataFilterValidationError,
    validate_metadata_document,
    validate_metadata_filters,
)

SCHEMA = {"product": "string", "revision": "integer"}


def test_flat_filter_mapping_becomes_predicates():
    """A flat {field: value} filter must actually filter.

    Regression: `filters` was typed as MetadataFilterGroup, so a flat dict
    validated as an *empty* group — the filter was dropped and the caller
    silently received unfiltered results.
    """
    request = RetrievalSearchRequest(
        collection_id="col_x", query="seals", filters={"product": "Dura 25"}
    )
    metadata = request.to_query("scope").filters.metadata
    assert metadata is not None, "flat filter mapping was dropped"
    assert [(p.field, p.value) for p in metadata.predicates] == [("product", "Dura 25")]


def test_filter_group_shape_still_accepted():
    request = RetrievalSearchRequest(
        collection_id="col_x",
        query="seals",
        filters={
            "logic": "and",
            "predicates": [{"field": "product", "operator": "equals", "value": "Dura 25"}],
            "groups": [],
        },
    )
    metadata = request.to_query("scope").filters.metadata
    assert metadata is not None
    assert metadata.predicates[0].field == "product"


def test_reserved_scope_field_rejected():
    request = RetrievalSearchRequest(
        collection_id="col_x", query="seals", filters={"owner_scope_id": "someone-else"}
    )
    metadata = request.to_query("scope").filters.metadata
    with pytest.raises(MetadataFilterValidationError, match="reserved"):
        validate_metadata_filters(metadata, SCHEMA)


def test_undeclared_field_rejected():
    request = RetrievalSearchRequest(
        collection_id="col_x", query="seals", filters={"not_in_schema": "x"}
    )
    metadata = request.to_query("scope").filters.metadata
    with pytest.raises(MetadataFilterValidationError, match="not declared"):
        validate_metadata_filters(metadata, SCHEMA)


def test_metadata_value_type_must_match_schema():
    """A declared integer field must reject a string at ingestion time."""
    with pytest.raises(MetadataFilterValidationError, match="expects integer"):
        validate_metadata_document({"revision": "not-an-integer"}, SCHEMA)


def test_metadata_value_of_correct_type_accepted():
    validate_metadata_document({"product": "Dura 25", "revision": 3}, SCHEMA)


def test_boolean_is_not_an_integer():
    """bool subclasses int in Python; a schema saying integer must still refuse it."""
    with pytest.raises(MetadataFilterValidationError, match="got boolean"):
        validate_metadata_document({"revision": True}, SCHEMA)
