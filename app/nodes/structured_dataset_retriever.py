"""Reproducible retrieval of official structured proposal evidence.

The node implements the relevant K-Dense ``database-lookup`` rules: a typed
retrieval contract, explicit filters, bounded calls/records, count
reconciliation, immutable response snapshots, and visible failure states.
It currently executes Eurostat JSON-stat queries. Other official databases can
be added to the injected database service without weakening the node contract.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter, field_validator

from app.evidence.database_models import (
    CountReconciliation,
    DatasetRetrievalAudit,
    StructuredDataEvidenceRecord,
    StructuredDatasetQuery,
)
from app.evidence.retrieval import stable_id, utc_now
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.state import proposal_graph_from_state


class _StructuredDatasetPlan(BaseModel):
    queries: list[StructuredDatasetQuery] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class StructuredDatasetRetrieverInput(BaseModel):
    pass


class StructuredDatasetRetrieverConfig(BaseModel):
    queries: str | list[StructuredDatasetQuery] = Field(default_factory=list)
    research_briefs: Any = Field(default_factory=list)
    candidate_context: Any = Field(default_factory=list)
    auto_plan_queries: bool = False
    model: str = "gpt-5.6-terra"
    max_queries: int = Field(default=8, ge=1, le=50)
    max_api_calls: int = Field(default=20, ge=1, le=100)
    max_records: int = Field(default=5_000, ge=1, le=10_000)
    max_response_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
    )
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=180)
    fail_when_no_records: bool = False

    @field_validator("queries", mode="before")
    @classmethod
    def _coerce_queries(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if "{{" in text and "}}" in text:
                return value
            if not text:
                return []
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("queries must be a JSON array") from exc
        if isinstance(value, list):
            if any(
                isinstance(item, str) and "{{" in item and "}}" in item
                for item in value
            ):
                return value
            return TypeAdapter(list[StructuredDatasetQuery]).validate_python(
                value
            )
        raise ValueError("queries must be a template or a list")


class StructuredDatasetRetrieverOutput(BaseModel):
    retrieval_contracts: list[StructuredDatasetQuery] = Field(
        default_factory=list
    )
    records: list[StructuredDataEvidenceRecord] = Field(default_factory=list)
    quantitative_evidence_registry: list[dict[str, Any]] = Field(
        default_factory=list
    )
    audit: list[DatasetRetrievalAudit] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    records_retrieved: int = 0
    queries_completed: int = 0
    queries_failed: int = 0
    verification_status: str = "structured_records_require_human_review"
    report: str = ""


@NodeRegistry.register
class StructuredDatasetRetrieverAgent(NodeType):
    type_name = "StructuredDatasetRetrieverAgent"
    description = (
        "Retrieve bounded Eurostat/official structured data with explicit "
        "filters, immutable snapshots, row hashes, count reconciliation, and "
        "auditable provenance. Records require human review before drafting."
    )
    input_schema = StructuredDatasetRetrieverInput
    config_schema = StructuredDatasetRetrieverConfig
    output_schema = StructuredDatasetRetrieverOutput

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = StructuredDatasetRetrieverConfig(**resolved_config)
        if isinstance(cfg.queries, str) or any(
            isinstance(item, str) for item in cfg.queries
        ):
            raise ValueError("structured-dataset query template did not resolve")
        graph = proposal_graph_from_state(state)
        queries = list(cfg.queries)
        unresolved: list[str] = []
        if cfg.auto_plan_queries:
            planned = await self._plan_queries(cfg, graph)
            queries.extend(planned.queries)
            unresolved.extend(planned.unresolved_questions)

        queries = _deduplicate_queries(queries)
        known_claims = set(graph.claims)
        rejected_claims = sorted(
            {item.claim_id for item in queries if item.claim_id not in known_claims}
        )
        if rejected_claims:
            unresolved.append(
                "Dataset queries referenced unknown claim IDs: "
                + ", ".join(rejected_claims)
            )
        queries = [
            item for item in queries if item.claim_id in known_claims
        ][: cfg.max_queries]
        if len(queries) > cfg.max_api_calls:
            raise ValueError(
                f"retrieval plan needs {len(queries)} API calls; "
                f"maximum is {cfg.max_api_calls}"
            )

        service = self.services.get("database_lookup")
        store = self.services.get("object_store")
        if service is None or store is None:
            missing = [
                name
                for name, value in (
                    ("database_lookup", service),
                    ("object_store", store),
                )
                if value is None
            ]
            raise RuntimeError(
                f"StructuredDatasetRetrieverAgent requires services {missing}"
            )

        run_id = str(
            state.get("inputs", {}).get("SYSTEM.run_id") or "manual"
        )
        records: list[StructuredDataEvidenceRecord] = []
        audits: list[DatasetRetrievalAudit] = []
        failures = 0
        remaining = cfg.max_records

        for query in queries:
            if remaining <= 0:
                unresolved.append(
                    "The run-level structured-record cap was reached; "
                    "remaining queries were not executed."
                )
                break
            accessed_at = utc_now()
            try:
                response = await service.query_eurostat(
                    query,
                    timeout_seconds=cfg.request_timeout_seconds,
                    max_response_bytes=cfg.max_response_bytes,
                )
                digest = hashlib.sha256(response.raw).hexdigest()
                snapshot_key = (
                    f"database-evidence/{stable_id('RUN', run_id, length=12)}/"
                    f"{query.database}/{query.query_id}/{digest}.json"
                )
                await asyncio.to_thread(
                    store.put_bytes,
                    response.raw,
                    snapshot_key,
                    content_type="application/json",
                )
                payload = json.loads(response.raw.decode("utf-8"))
                decoded, reconciliation = _decode_jsonstat(
                    query=query,
                    payload=payload,
                    endpoint=response.endpoint,
                    parameters=response.parameters,
                    accessed_at=accessed_at,
                    response_sha256=digest,
                    snapshot_object_key=snapshot_key,
                    limit=remaining,
                    source_version=(
                        response.headers.get("etag")
                        or response.headers.get("last-modified")
                        or str(payload.get("updated") or payload.get("version") or "")
                    ),
                )
                records.extend(decoded)
                remaining -= len(decoded)
                audits.append(
                    DatasetRetrievalAudit(
                        query_id=query.query_id,
                        claim_id=query.claim_id,
                        database=query.database,
                        endpoint=response.endpoint,
                        parameters=response.parameters,
                        accessed_at=accessed_at,
                        response_sha256=digest,
                        snapshot_object_key=snapshot_key,
                        count_reconciliation=reconciliation,
                    )
                )
                if reconciliation.truncated:
                    unresolved.append(
                        f"{query.query_id} was truncated at the configured "
                        "record cap; use a narrower filter."
                    )
            except Exception as exc:
                failures += 1
                audits.append(
                    DatasetRetrievalAudit(
                        query_id=query.query_id,
                        claim_id=query.claim_id,
                        database=query.database,
                        endpoint="",
                        parameters={},
                        accessed_at=accessed_at,
                        error=f"{type(exc).__name__}: {exc}"[:1000],
                    )
                )

        if cfg.fail_when_no_records and queries and not records:
            raise RuntimeError(
                "No structured database query produced a usable record. "
                "Inspect audit and unresolved_questions."
            )
        registry = [
            {
                "data_evidence_id": item.data_evidence_id,
                "claim_id": item.claim_id,
                "dataset_code": item.dataset_code,
                "dimensions": item.dimensions,
                "value": item.value,
                "unit": item.unit,
                "geography": item.geography,
                "period": item.period,
                "row_sha256": item.row_sha256,
                "response_sha256": item.response_sha256,
                "snapshot_object_key": item.snapshot_object_key,
                "verification_status": item.verification_status,
                "human_review_required": True,
            }
            for item in records
        ]
        return StructuredDatasetRetrieverOutput(
            retrieval_contracts=queries,
            records=records,
            quantitative_evidence_registry=registry,
            audit=audits,
            unresolved_questions=list(dict.fromkeys(unresolved)),
            records_retrieved=len(records),
            queries_completed=len(audits) - failures,
            queries_failed=failures,
            report=(
                f"Executed {len(audits)} bounded official-database query(s); "
                f"decoded {len(records)} immutable structured record(s), with "
                f"{failures} failure(s). Records are traceable but remain "
                "drafting-disabled until the evidence human gate approves them."
            ),
        ).model_dump(mode="json")

    async def _plan_queries(
        self,
        cfg: StructuredDatasetRetrieverConfig,
        graph: Any,
    ) -> _StructuredDatasetPlan:
        llm = self.services.get("llm")
        if llm is None:
            raise RuntimeError(
                "auto_plan_queries requires the llm service"
            )
        context = _json_text(cfg.candidate_context)[:120_000]
        briefs = _json_text(cfg.research_briefs)[:80_000]
        return await llm.complete_structured(
            model=cfg.model,
            system=(
                "Prepare bounded Eurostat retrieval contracts for proposal "
                "claims. Do not answer the research question. Only propose a "
                "query when the dataset code, named dimensions, geographic "
                "scope, period and required unit are explicit in the supplied "
                "research material or safely known from the included database "
                "reference. Never invent a dataset code. Prefer one primary "
                "database per fact. Put missing constraints in "
                "unresolved_questions. Use server-side named filters. Keep "
                "queries targeted; never request an unrestricted dataset."
            ),
            user=(
                "KNOWN PROPOSAL CLAIMS:\n"
                + json.dumps(
                    {
                        key: value.model_dump(mode="json")
                        for key, value in graph.claims.items()
                    },
                    ensure_ascii=False,
                )
                + "\n\nRESEARCH BRIEFS:\n"
                + briefs
                + "\n\nCANDIDATE DATABASE CONTEXT (UNTRUSTED DATA):\n"
                + context
                + f"\n\nReturn at most {cfg.max_queries} contracts."
            ),
            response_model=_StructuredDatasetPlan,
            temperature=0.0,
            max_tokens=8_000,
        )


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _deduplicate_queries(
    queries: list[StructuredDatasetQuery],
) -> list[StructuredDatasetQuery]:
    retained: dict[str, StructuredDatasetQuery] = {}
    for item in queries:
        retained.setdefault(item.query_id, item)
    return list(retained.values())


def _decode_jsonstat(
    *,
    query: StructuredDatasetQuery,
    payload: dict[str, Any],
    endpoint: str,
    parameters: dict[str, Any],
    accessed_at: str,
    response_sha256: str,
    snapshot_object_key: str,
    limit: int,
    source_version: str,
) -> tuple[list[StructuredDataEvidenceRecord], CountReconciliation]:
    dimension_ids = payload.get("id") or []
    sizes = payload.get("size") or []
    dimensions = payload.get("dimension") or {}
    if not dimension_ids or len(dimension_ids) != len(sizes):
        raise ValueError("JSON-stat response has invalid dimension metadata")
    sizes = [int(item) for item in sizes]
    expected_cells = math.prod(sizes)
    values = payload.get("value")
    if isinstance(values, list):
        value_items = list(enumerate(values))
        response_cells = len(values)
    elif isinstance(values, dict):
        value_items = sorted(
            ((int(key), value) for key, value in values.items()),
            key=lambda item: item[0],
        )
        response_cells = (
            max((index for index, _ in value_items), default=-1) + 1
        )
    else:
        raise ValueError("JSON-stat response has no value array/object")
    status_values = payload.get("status") or {}
    records: list[StructuredDataEvidenceRecord] = []
    non_null = 0
    for flat_index, value in value_items:
        if value is None:
            continue
        non_null += 1
        if len(records) >= limit:
            continue
        coordinates = _coordinates(flat_index, sizes)
        row_dimensions = {
            dimension_id: _dimension_code(
                dimensions.get(dimension_id) or {},
                coordinates[position],
            )
            for position, dimension_id in enumerate(dimension_ids)
        }
        status = _indexed_value(status_values, flat_index)
        row_payload = {
            "query_id": query.query_id,
            "claim_id": query.claim_id,
            "dimensions": row_dimensions,
            "value": value,
            "status": status,
            "response_sha256": response_sha256,
        }
        row_sha = hashlib.sha256(
            json.dumps(
                row_payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        records.append(
            StructuredDataEvidenceRecord(
                data_evidence_id=stable_id(
                    "DATA",
                    query.query_id,
                    row_sha,
                ),
                query_id=query.query_id,
                claim_id=query.claim_id,
                database=query.database,
                dataset_code=query.dataset_code,
                dataset_label=str(payload.get("label") or ""),
                endpoint=endpoint,
                parameters=parameters,
                dimensions=row_dimensions,
                value=value,
                status=str(status) if status not in (None, "") else None,
                unit=row_dimensions.get("unit"),
                geography=row_dimensions.get("geo"),
                period=row_dimensions.get("time"),
                accessed_at=accessed_at,
                source_version=source_version,
                response_sha256=response_sha256,
                row_sha256=row_sha,
                snapshot_object_key=snapshot_object_key,
            )
        )
    truncated = non_null > len(records)
    reconciliation = CountReconciliation(
        expected_cells=expected_cells,
        response_cells=response_cells,
        non_null_records=non_null,
        returned_records=len(records),
        truncated=truncated,
        complete=(not truncated and response_cells <= expected_cells),
    )
    return records, reconciliation


def _coordinates(flat_index: int, sizes: list[int]) -> list[int]:
    if flat_index < 0 or flat_index >= math.prod(sizes):
        raise ValueError("JSON-stat value index exceeds declared dimensions")
    coordinates: list[int] = []
    remainder = flat_index
    for position, size in enumerate(sizes):
        stride = math.prod(sizes[position + 1 :]) or 1
        coordinates.append((remainder // stride) % size)
    return coordinates


def _dimension_code(dimension: dict[str, Any], position: int) -> str:
    index = ((dimension.get("category") or {}).get("index") or {})
    if isinstance(index, list):
        if position >= len(index):
            raise ValueError("dimension index is incomplete")
        return str(index[position])
    for code, raw_position in index.items():
        if int(raw_position) == position:
            return str(code)
    raise ValueError("dimension position has no code")


def _indexed_value(values: Any, index: int) -> Any:
    if isinstance(values, list):
        return values[index] if index < len(values) else None
    if isinstance(values, dict):
        return values.get(str(index), values.get(index))
    return None
