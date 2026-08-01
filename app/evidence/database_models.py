"""Typed provenance contracts for structured and internal proposal evidence.

These records deliberately remain separate from :class:`VerifiedClaim`.
PaperQA2 and prior-project search results are candidate material, literature
claims require passage verification, structured API rows require reproducible
query provenance, and partner/internal facts require explicit human approval.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:+-]{1,120}$")
_SAFE_PERIOD = re.compile(r"^[0-9]{4}(?:-(?:Q[1-4]|[0-9]{2}))?$")


class StructuredDatasetQuery(BaseModel):
    """One bounded, reproducible public-database retrieval contract."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    claim_id: str
    database: Literal["eurostat"] = "eurostat"
    dataset_code: str
    target: str
    scope: Literal["targeted", "exhaustive"] = "targeted"
    filters: dict[str, list[str]] = Field(default_factory=dict)
    start_period: str | None = None
    end_period: str | None = None
    required_fields: list[str] = Field(default_factory=list)
    expected_unit: str | None = None
    notes: str = ""

    @field_validator("query_id", "claim_id", "dataset_code")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        value = value.strip()
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError(
                "identifier must contain only letters, numbers, '.', '_', "
                "':', '+', or '-' and be at most 120 characters"
            )
        return value

    @field_validator("start_period", "end_period")
    @classmethod
    def _safe_period(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not _SAFE_PERIOD.fullmatch(value):
            raise ValueError(
                "period must be YYYY, YYYY-Q1..Q4, or YYYY-MM"
            )
        return value

    @field_validator("filters")
    @classmethod
    def _safe_filters(
        cls,
        value: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        cleaned: dict[str, list[str]] = {}
        for field_name, raw_values in value.items():
            if not _SAFE_IDENTIFIER.fullmatch(field_name):
                raise ValueError(f"unsafe filter field {field_name!r}")
            values = []
            for raw in raw_values:
                item = str(raw).strip()
                if not _SAFE_IDENTIFIER.fullmatch(item):
                    raise ValueError(
                        f"unsafe value {item!r} for filter {field_name!r}"
                    )
                values.append(item)
            if not values:
                raise ValueError(
                    f"filter {field_name!r} must contain at least one value"
                )
            cleaned[field_name] = list(dict.fromkeys(values))
        return cleaned


class CountReconciliation(BaseModel):
    expected_cells: int = 0
    response_cells: int = 0
    non_null_records: int = 0
    returned_records: int = 0
    truncated: bool = False
    complete: bool = False


class StructuredDataEvidenceRecord(BaseModel):
    """One value decoded from a versioned official-database response."""

    data_evidence_id: str
    query_id: str
    claim_id: str
    database: str
    dataset_code: str
    dataset_label: str = ""
    endpoint: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    dimensions: dict[str, str] = Field(default_factory=dict)
    value: int | float | str | None = None
    status: str | None = None
    unit: str | None = None
    geography: str | None = None
    period: str | None = None
    accessed_at: str
    source_version: str = ""
    response_sha256: str
    row_sha256: str
    snapshot_object_key: str
    verification_status: Literal["verified_structured_record"] = (
        "verified_structured_record"
    )
    human_review_required: bool = True
    drafting_allowed: bool = False


class DatasetRetrievalAudit(BaseModel):
    query_id: str
    claim_id: str
    database: str
    endpoint: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    accessed_at: str
    response_sha256: str = ""
    snapshot_object_key: str = ""
    count_reconciliation: CountReconciliation = Field(
        default_factory=CountReconciliation
    )
    error: str | None = None


class InternalEvidenceRecord(BaseModel):
    """Exact-passage-matched fact from an internal or partner source."""

    internal_evidence_id: str
    question: str
    fact_key: str
    fact_value: Any
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_graph_object_ids: list[str] = Field(default_factory=list)
    source_id: str
    source_name: str
    source_class: str
    exact_passage: str
    locator: str = ""
    source_sha256: str
    record_sha256: str
    verification_status: Literal[
        "approved_internal",
        "exact_passage_matched_pending_human_approval",
    ]
    human_review_required: bool = True
    drafting_allowed: bool = False


class InternalEvidenceReviewItem(BaseModel):
    internal_evidence_id: str
    source_name: str
    fact_key: str
    fact_value: Any
    decision: Literal["approve", "correct", "reject"] = "approve"

