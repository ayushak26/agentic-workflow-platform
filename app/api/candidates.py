"""Candidates viewer API: list a run's acquired evidence PDFs with open links,
plus the raw discovery/Deep Research/prior-project/official-database
candidates (name + URL) recorded for the run, since
ScholarlyCandidateDiscoveryAgent, BoundedDeepResearchAgent,
PriorProjectRetrieverAgent, and StructuredDatasetRetrieverAgent all produce
either the same CandidateSource shape or a reconstructible exact-query URL
regardless of source type.

InternalProjectEvidenceRetrieverAgent facts have no locator at all (only a
source_name and an exact_passage) — those are surfaced separately as
"internal evidence" with a human-triggered secondary verification action
(see app.evidence.claim_verification) instead of a URL.

MinIO listing stays Mongo-free (Build A): reads the evidence/{run_id}/
prefix directly. The discovered/internal-evidence additions need
run_history (Mongo) and degrade to an empty list, not a failure, if that's
unavailable — so a run with acquired PDFs but no reachable Mongo still
renders.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.evidence.claim_verification import verify_claim
from app.security.dependencies import CurrentUser, require_consultant
from app.storage.minio_client import get_object_store
from app.workflow.claim_verifications import get_verifications, save_verification
from app.workflow.run_history import get_run

router = APIRouter(prefix="/api/candidates", tags=["candidates"])

# evidence/{run_id}/SRC-xxxx/VER-yyyy.pdf
_KEY_RE = re.compile(
    r"^evidence/(?P<run>[^/]+)/(?P<src>SRC-[^/]+)/(?P<ver>VER-[^/.]+)\.pdf$"
)

_DISCOVERY_NODE_TYPES = {
    "ScholarlyCandidateDiscoveryAgent",
    "BoundedDeepResearchAgent",
    "PriorProjectRetrieverAgent",
}

_INTERNAL_EVIDENCE_NODE_TYPE = "InternalProjectEvidenceRetrieverAgent"
_STRUCTURED_DATASET_NODE_TYPE = "StructuredDatasetRetrieverAgent"


def _discovery_candidate_url(candidate: dict[str, Any]) -> str | None:
    """Internal helper for the discovery candidate url step.

    Args:
        candidate (dict[str, Any]): The candidate.

    Returns:
        str | None: The candidate url.
    """
    url = candidate.get("canonical_url") or candidate.get("pdf_url")
    if url:
        return str(url)
    doi = candidate.get("doi")
    if doi:
        return f"https://doi.org/{doi}"
    return None


def _discovered_candidates_from_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Every discovery/Deep Research/prior-project candidate this run ever
    produced.

    Sourced from node_runs (durable in run_history, not just live state).
    Deduplicated by candidate_id since the same source can surface from more
    than one discovery node. These are candidates, never verified evidence
    — that distinction is preserved by carrying purpose/authority/
    retraction_status through untouched.
    """

    by_id: dict[str, dict[str, Any]] = {}
    for node in (run.get("node_runs") or {}).values():
        if not isinstance(node, dict):
            continue
        if node.get("type_name") not in _DISCOVERY_NODE_TYPES:
            continue
        output = node.get("output") or {}
        for raw in output.get("candidates") or []:
            candidate_id = raw.get("candidate_id")
            if not candidate_id or candidate_id in by_id:
                continue
            by_id[candidate_id] = {
                "candidate_id": candidate_id,
                "claim_id": raw.get("claim_id"),
                "title": raw.get("title") or "(untitled)",
                "url": _discovery_candidate_url(raw),
                "doi": raw.get("doi"),
                "source": raw.get("source"),
                "purpose": raw.get("purpose"),
                "authority": raw.get("authority"),
                "retraction_status": raw.get("retraction_status"),
                "found_by_node_id": node.get("node_id"),
                "found_by_type": node.get("type_name"),
            }

    return list(by_id.values())


def _structured_dataset_query_url(record: dict[str, Any]) -> str | None:
    """Reconstruct the exact bounded Eurostat query that produced this row.

    ``endpoint`` is the bare dataset URL; the actual filters used are stored
    separately in ``parameters`` (a JSON-stat query accepts repeated named
    values, so a filter can be a list). Rebuilding the full URL gives a
    human a one-click, byte-for-byte reproduction of the retrieval —
    stronger than a plain "trust me" citation.
    """

    endpoint = record.get("endpoint")
    if not endpoint:
        return None
    pairs: list[tuple[str, str]] = []
    for key, value in (record.get("parameters") or {}).items():
        values = value if isinstance(value, list) else [value]
        pairs.extend((key, str(item)) for item in values)
    query = urlencode(pairs)
    return f"{endpoint}?{query}" if query else str(endpoint)


def _structured_dataset_candidates_from_run(
    run: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """StructuredDatasetRetrieverAgent rows, reshaped into the same
    discovered-candidate table shape as web-search-based discovery. Each row
    does have a real, reproducible URL (the exact Eurostat API call), unlike
    internal evidence — so it belongs with the other URL-bearing sources,
    not with the "needs verification" list.
    """

    # Keyed by query_id, not by individual row: one bounded query can decode
    # into hundreds of rows (one per geo/time/dimension combination), and
    # they all share the exact same reproducible URL — a reviewer needs one
    # entry per query to check, not one per data point.
    by_query: dict[str, dict[str, Any]] = {}
    row_counts: dict[str, int] = {}
    for node in (run.get("node_runs") or {}).values():
        if not isinstance(node, dict) or node.get("type_name") != _STRUCTURED_DATASET_NODE_TYPE:
            continue
        output = node.get("output") or {}
        for raw in output.get("records") or []:
            query_id = raw.get("query_id") or raw.get("data_evidence_id")
            if not query_id:
                continue
            row_counts[query_id] = row_counts.get(query_id, 0) + 1
            if query_id in by_query:
                continue
            label = raw.get("dataset_label") or raw.get("dataset_code") or "Official dataset"
            by_query[query_id] = {
                "candidate_id": query_id,
                "claim_id": raw.get("claim_id"),
                "title": label,
                "url": _structured_dataset_query_url(raw),
                "doi": None,
                "source": raw.get("database"),
                "purpose": "structured_data",
                "authority": "official_eu",
                "retraction_status": None,
                "found_by_node_id": node.get("node_id"),
                "found_by_type": node.get("type_name"),
            }

    for query_id, item in by_query.items():
        count = row_counts.get(query_id, 0)
        item["title"] = f"{item['title']} — {count} record{'s' if count != 1 else ''}"

    return by_query


def _internal_evidence_from_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    """InternalProjectEvidenceRetrieverAgent facts: no URL exists for these,
    only a source name and an exact quoted passage — the UI offers a
    secondary "verify this claim" action for them instead of a link.
    """

    by_id: dict[str, dict[str, Any]] = {}
    for node in (run.get("node_runs") or {}).values():
        if not isinstance(node, dict) or node.get("type_name") != _INTERNAL_EVIDENCE_NODE_TYPE:
            continue
        output = node.get("output") or {}
        for raw in output.get("records") or []:
            record_id = raw.get("internal_evidence_id")
            if not record_id or record_id in by_id:
                continue
            linked_claims = raw.get("linked_claim_ids") or []
            by_id[record_id] = {
                "record_id": record_id,
                "claim_id": linked_claims[0] if linked_claims else None,
                "fact_key": raw.get("fact_key"),
                "content": raw.get("exact_passage") or str(raw.get("fact_value") or ""),
                "source_name": raw.get("source_name"),
                "source_class": raw.get("source_class"),
                "verification_status": raw.get("verification_status"),
                "drafting_allowed": raw.get("drafting_allowed"),
                "found_by_node_id": node.get("node_id"),
            }

    return sorted(
        by_id.values(),
        key=lambda item: (item.get("claim_id") or "", item.get("fact_key") or ""),
    )


class VerifyClaimRequest(BaseModel):
    """Pydantic model defining the VerifyClaimRequest shape.

    Attributes:
        record_id (str).
    """
    record_id: str


@router.get("/{run_id}")
async def list_candidates(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Every acquired evidence PDF for a run, plus discovered candidates."""
    # Guard against traversal / odd ids in the prefix.
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
        raise HTTPException(status_code=400, detail="invalid run_id")

    store = get_object_store()
    prefix = f"evidence/{run_id}/"
    objects = store.list_objects(prefix)

    candidates: list[dict] = []
    for obj in objects:
        m = _KEY_RE.match(obj["key"])
        if not m:
            continue  # skip .pages.json and anything non-PDF
        candidates.append(
            {
                "source_id": m.group("src"),
                "version_id": m.group("ver"),
                "key": obj["key"],
                "size": obj["size"],
                "last_modified": obj["last_modified"],
                # short-lived open link straight to object storage
                "pdf_url": store.presigned_url(obj["key"], expires_seconds=3600),
            }
        )

    candidates.sort(key=lambda c: c["source_id"])

    discovered: list[dict[str, Any]] = []
    internal_evidence: list[dict[str, Any]] = []
    services = getattr(request.app.state, "services", {})
    db = services.get("audit_db")
    if db is not None:
        scope = getattr(user, "session_id", None) or user.username
        try:
            run = await get_run(db, scope, run_id)
        except Exception:
            run = None
        if run is not None:
            by_id = {
                candidate["candidate_id"]: candidate
                for candidate in _discovered_candidates_from_run(run)
            }
            by_id.update(_structured_dataset_candidates_from_run(run))
            discovered = sorted(
                by_id.values(),
                key=lambda item: (item.get("claim_id") or "", item.get("title") or ""),
            )
            internal_evidence = _internal_evidence_from_run(run)
            try:
                verifications = await get_verifications(db, run_id)
            except Exception:
                verifications = {}
            for item in internal_evidence:
                item["verification"] = verifications.get(item["record_id"])

    return {
        "run_id": run_id,
        "count": len(candidates),
        "candidates": candidates,
        "discovered_count": len(discovered),
        "discovered_candidates": discovered,
        "internal_evidence_count": len(internal_evidence),
        "internal_evidence": internal_evidence,
    }


@router.post("/{run_id}/verify-claim")
async def verify_claim_endpoint(
    run_id: str,
    body: VerifyClaimRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Run the gpt-5.6-sol secondary check for one internal-evidence record.

    The claim text is re-read from the stored run record by record_id (never
    trusted from the request body) so this endpoint can't be repurposed into
    an open "verify arbitrary text" oracle.
    """

    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
        raise HTTPException(status_code=400, detail="invalid run_id")

    services = getattr(request.app.state, "services", {})
    db = services.get("audit_db")
    if db is None:
        raise HTTPException(status_code=503, detail="run history database is unavailable")

    scope = getattr(user, "session_id", None) or user.username
    run = await get_run(db, scope, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    record = next(
        (
            item
            for item in _internal_evidence_from_run(run)
            if item["record_id"] == body.record_id
        ),
        None,
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="internal evidence record not found in this run",
        )

    llm = services.get("llm")
    web_search = services.get("web_search")
    if llm is None or web_search is None:
        raise HTTPException(
            status_code=503,
            detail="llm and web_search services are required to verify a claim",
        )

    result = await verify_claim(
        record["content"],
        source_name=record["source_name"] or "unknown source",
        llm=llm,
        web_search=web_search,
    )
    payload = result.model_dump(mode="json")
    await save_verification(db, run_id, body.record_id, payload)
    return {"record_id": body.record_id, "result": payload}
