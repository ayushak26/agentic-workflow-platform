"""Candidates viewer API: list a run's acquired evidence PDFs with open links,
plus the raw discovery/Deep Research candidates (name + URL) recorded for
the run — web, database, and research-paper sources alike, since
ScholarlyCandidateDiscoveryAgent and BoundedDeepResearchAgent both produce
the same CandidateSource shape regardless of source type.

MinIO listing stays Mongo-free (Build A): reads the evidence/{run_id}/
prefix directly. The discovered-candidates addition needs run_history
(Mongo) and degrades to an empty list, not a failure, if that's unavailable
— so a run with acquired PDFs but no reachable Mongo still renders.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.security.dependencies import CurrentUser, require_consultant
from app.storage.minio_client import get_object_store
from app.workflow.run_history import get_run

router = APIRouter(prefix="/api/candidates", tags=["candidates"])

# evidence/{run_id}/SRC-xxxx/VER-yyyy.pdf
_KEY_RE = re.compile(
    r"^evidence/(?P<run>[^/]+)/(?P<src>SRC-[^/]+)/(?P<ver>VER-[^/.]+)\.pdf$"
)

_DISCOVERY_NODE_TYPES = {
    "ScholarlyCandidateDiscoveryAgent",
    "BoundedDeepResearchAgent",
}


def _discovery_candidate_url(candidate: dict[str, Any]) -> str | None:
    url = candidate.get("canonical_url") or candidate.get("pdf_url")
    if url:
        return str(url)
    doi = candidate.get("doi")
    if doi:
        return f"https://doi.org/{doi}"
    return None


def _discovered_candidates_from_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Every discovery/Deep Research candidate this run ever produced.

    Sourced from node_runs (durable in run_history, not just live state).
    Deduplicated by candidate_id since the same source can surface from both
    a scholarly-discovery node and a Deep Research node. These are
    candidates, never verified evidence — that distinction is preserved by
    carrying purpose/authority/retraction_status through untouched.
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

    return sorted(
        by_id.values(),
        key=lambda item: (item.get("claim_id") or "", item.get("title") or ""),
    )


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
    services = getattr(request.app.state, "services", {})
    db = services.get("audit_db")
    if db is not None:
        scope = getattr(user, "session_id", None) or user.username
        try:
            run = await get_run(db, scope, run_id)
        except Exception:
            run = None
        if run is not None:
            discovered = _discovered_candidates_from_run(run)

    return {
        "run_id": run_id,
        "count": len(candidates),
        "candidates": candidates,
        "discovered_count": len(discovered),
        "discovered_candidates": discovered,
    }
