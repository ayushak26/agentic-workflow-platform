"""Typed metadata filters and the non-removable authorization-filter clauses.

Two filter trees exist and they are never mixed at the caller level:

* **Security clauses** — owner/session scope, logical collection, resolved
  physical index, and (optionally) "exclude parent chunks from initial
  candidates". These come only from server-resolved state and are always
  ANDed onto the query; nothing here ever exposes a way to remove them.
* **User metadata filters** — the typed ``MetadataFilterGroup`` tree a
  caller, a self-query transform, or a Playground filter box supplies. These
  are validated against the Collection's ``metadata_schema`` and can only
  narrow the candidate set the security clauses already scoped.
"""
from __future__ import annotations

import re
from typing import Any

from weaviate.classes.query import Filter

from app.retrieval.models import MetadataFilterGroup, MetadataFilterPredicate, RetrievalFilters

# Fields that identify security/provenance scope. A caller-supplied filter,
# a self-query transform, or a runtime_filters dict may never touch these —
# they are compiled exclusively from server-resolved state.
RESERVED_METADATA_FIELDS = frozenset({
    "workspace_id", "session_id", "owner_scope_id", "collection_id",
    "index_id", "document_id", "source_id", "source_version_id",
})

# Metadata fields every Collection may use without declaring them in its own
# schema, and the type they must be treated as.
STANDARD_METADATA_TYPES: dict[str, str] = {
    "department": "string",
    "document_type": "string",
    "country": "string",
    "product": "string",
    "version": "string",
    "publish_date": "date",
    "customer": "string",
    "industry": "string",
    "doc_type": "string",
    "language": "string",
}

_PROPERTY_NAME_RE = re.compile(r"[^a-z0-9_]+")


def metadata_property_name(key: str) -> str:
    """Sanitize a user-supplied metadata key into a safe Weaviate property name."""

    name = _PROPERTY_NAME_RE.sub("_", key.strip().lower()).strip("_") or "field"
    if name[0].isdigit():
        name = f"m_{name}"
    return name


class MetadataFilterValidationError(ValueError):
    pass


def _field_type(field: str, schema: dict[str, Any]) -> str | None:
    if field in STANDARD_METADATA_TYPES:
        return STANDARD_METADATA_TYPES[field]
    properties = schema.get("properties", schema) if isinstance(schema, dict) else {}
    definition = properties.get(field) if isinstance(properties, dict) else None
    if definition is None:
        return None
    return definition.get("type") if isinstance(definition, dict) else definition


def _check_field(field: str, schema: dict[str, Any]) -> None:
    if field in RESERVED_METADATA_FIELDS:
        raise MetadataFilterValidationError(
            f"metadata field {field!r} is a reserved security/provenance field"
        )
    if _field_type(field, schema) is None:
        raise MetadataFilterValidationError(
            f"metadata field {field!r} is not declared in the collection's metadata schema"
        )


def validate_metadata_document(metadata: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate an ingestion-time metadata document against a Collection schema."""

    for field in metadata:
        _check_field(str(field), schema)


def _validate_group(group: MetadataFilterGroup, schema: dict[str, Any]) -> None:
    for predicate in group.predicates:
        _check_field(predicate.field, schema)
    for nested in group.groups:
        _validate_group(nested, schema)


def validate_metadata_filters(
    metadata: MetadataFilterGroup | None, schema: dict[str, Any]
) -> None:
    """Validate a caller-supplied (or self-query-generated) filter tree.

    Every leaf field must be either a standard field or declared in the
    collection's own metadata_schema, and none may be a reserved
    security/provenance field. Called before a query reaches the datastore.
    """

    if metadata is None:
        return
    _validate_group(metadata, schema)


def coerce_metadata_filter_group(
    value: dict[str, Any] | MetadataFilterGroup | None,
) -> MetadataFilterGroup | None:
    """Normalize workflow ``runtime_filters``/API filter payloads to one shape.

    Accepts an already-typed ``MetadataFilterGroup``, a dict already shaped
    like one (``{"logic": ..., "predicates": [...]}``), or a flat mapping of
    ``field -> value`` (the common workflow ``runtime_filters`` shape), which
    becomes an ANDed set of ``equals`` predicates.
    """

    if value is None:
        return None
    if isinstance(value, MetadataFilterGroup):
        return value if (value.predicates or value.groups) else None
    if not value:
        return None
    if "predicates" in value or "groups" in value or "logic" in value:
        return MetadataFilterGroup.model_validate(value)
    return MetadataFilterGroup(
        logic="and",
        predicates=[
            MetadataFilterPredicate(field=str(field), operator="equals", value=filter_value)
            for field, filter_value in value.items()
        ],
    )


def _predicate_to_filter(predicate: MetadataFilterPredicate) -> Filter:
    property_name = metadata_property_name(predicate.field)
    prop = Filter.by_property(property_name)
    operator = predicate.operator
    value = predicate.value
    if operator == "equals":
        return prop.equal(value)
    if operator == "not_equals":
        return prop.not_equal(value)
    if operator == "in":
        return Filter.any_of([prop.equal(item) for item in value])
    if operator == "not_in":
        return Filter.all_of([prop.not_equal(item) for item in value])
    if operator == "contains_any":
        return prop.contains_any(list(value))
    if operator == "greater_than":
        return prop.greater_than(value)
    if operator == "less_than":
        return prop.less_than(value)
    if operator == "between":
        low, high = value
        return Filter.all_of([prop.greater_or_equal(low), prop.less_or_equal(high)])
    raise MetadataFilterValidationError(f"unsupported filter operator {operator!r}")


def _group_to_filter(group: MetadataFilterGroup) -> Filter | None:
    clauses: list[Filter] = [_predicate_to_filter(p) for p in group.predicates]
    for nested in group.groups:
        nested_filter = _group_to_filter(nested)
        if nested_filter is not None:
            clauses.append(nested_filter)
    if not clauses:
        return None
    return Filter.any_of(clauses) if group.logic == "or" else Filter.all_of(clauses)


def build_secure_where_filter(
    filters: RetrievalFilters,
    *,
    index_id: str | None = None,
    exclude_parent_chunks: bool = False,
) -> Filter:
    """Compile the non-removable security clause, ANDed with user metadata.

    ``filters.session_id``/``collection_id`` pin owner and logical-collection
    scope; ``index_id`` (the physical index identifier) additionally pins
    the resolved index when one is known. ``exclude_parent_chunks`` keeps
    parent/child chunking's synthetic parent records out of the initial
    candidate set — they are only fetched later, during expansion.
    """

    clauses: list[Filter] = [
        Filter.by_property("session_id").equal(filters.session_id),
        Filter.by_property("collection_id").equal(filters.collection_id),
    ]
    if index_id:
        clauses.append(Filter.by_property("index_id").equal(index_id))
    if filters.industry:
        clauses.append(Filter.by_property("industry").equal(filters.industry))
    if filters.doc_types:
        clauses.append(Filter.by_property("doc_type").contains_any(filters.doc_types))
    if filters.collection_ids:
        clauses.append(Filter.by_property("collection_id").contains_any(filters.collection_ids))
    if filters.date_after:
        clauses.append(Filter.by_property("ingested_at").greater_than(filters.date_after))
    if filters.date_before:
        clauses.append(Filter.by_property("ingested_at").less_than(filters.date_before))
    if exclude_parent_chunks:
        clauses.append(Filter.by_property("chunk_role").not_equal("parent"))
    if filters.metadata is not None:
        user_filter = _group_to_filter(filters.metadata)
        if user_filter is not None:
            clauses.append(user_filter)
    return Filter.all_of(clauses)
