"""Candidates viewer API: list a run's acquired evidence PDFs with open links.

MinIO-only (Build A): reads the evidence/{run_id}/ prefix, keeps the .pdf per
source version, and returns a presigned URL for each. No run-state / Mongo
dependency, so it works for any run whose evidence is in object storage.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException

from app.security.dependencies import CurrentUser, require_consultant
from app.storage.minio_client import get_object_store

router = APIRouter(prefix="/api/candidates", tags=["candidates"])

# evidence/{run_id}/SRC-xxxx/VER-yyyy.pdf
_KEY_RE = re.compile(
    r"^evidence/(?P<run>[^/]+)/(?P<src>SRC-[^/]+)/(?P<ver>VER-[^/.]+)\.pdf$"
)


@router.get("/{run_id}")
async def list_candidates(
    run_id: str,
    user: CurrentUser = Depends(require_consultant),
):
    """Every acquired evidence PDF for a run, each with a presigned open URL."""
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
    return {
        "run_id": run_id,
        "count": len(candidates),
        "candidates": candidates,
    }
