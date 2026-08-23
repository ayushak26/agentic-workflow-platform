"""Immutable proposal snapshots, source versions, and approval decisions."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from app.proposal_graph.coverage import (
    CallCoverageMatrix,
    build_call_coverage_matrix,
)
from app.proposal_graph.graph import ProposalGraph


def _now() -> datetime:
    """Internal helper for the now step.

    Returns:
        datetime: The result.
    """
    return datetime.now(timezone.utc)


def _canonical_hash(payload: Any) -> str:
    """Internal helper for the canonical hash step.

    Args:
        payload (Any): Event or audit payload.

    Returns:
        str: The hash.
    """
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SourceVersionRecord(BaseModel):
    """Pydantic model defining the SourceVersionRecord shape.

    Attributes:
        proposal_id (str).
        source_id (str).
        version (int).
        version_id (str).
        content_sha256 (str).
        metadata_sha256 (str).
        object_key (str).
        title (str).
    """
    proposal_id: str
    source_id: str
    version: int
    version_id: str
    content_sha256: str
    metadata_sha256: str
    object_key: str
    title: str
    identifier: str | None = None
    authority: str = "unverified"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    created_by: str


class ProposalSnapshot(BaseModel):
    """Pydantic model defining the ProposalSnapshot shape.

    Attributes:
        proposal_id (str).
        version (int).
        snapshot_id (str).
        content_sha256 (str).
        graph (dict[str, Any]).
        created_at (datetime).
        created_by (str).
        reason (str).
    """
    proposal_id: str
    version: int
    snapshot_id: str
    content_sha256: str
    graph: dict[str, Any]
    created_at: datetime
    created_by: str
    reason: str


class ApprovalRecord(BaseModel):
    """Pydantic model defining the ApprovalRecord shape.

    Attributes:
        approval_id (str).
        proposal_id (str).
        stage (str).
        snapshot_id (str).
        snapshot_version (int).
        snapshot_sha256 (str).
        status (Literal['pending', 'approved', 'rejected', 'changes_requested']).
        selected_concept_id (str | None).
    """
    approval_id: str
    proposal_id: str
    stage: str
    snapshot_id: str
    snapshot_version: int
    snapshot_sha256: str
    status: Literal["pending", "approved", "rejected", "changes_requested"]
    selected_concept_id: str | None = None
    coverage: CallCoverageMatrix
    requested_by: str
    requested_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    comment: str | None = None


class ProposalWorkspaceStore:
    """Provides the ProposalWorkspaceStore behaviour."""
    def __init__(self, db, object_store) -> None:
        """Initialize the ProposalWorkspaceStore.

        Args:
            db: Mongo database handle.
            object_store: The object store.
        """
        self.db = db
        self.object_store = object_store

    async def ensure_indexes(self) -> None:
        """Ensure the indexes."""
        await self.db["proposal_source_versions"].create_index(
            [
                ("session_id", 1),
                ("proposal_id", 1),
                ("source_id", 1),
                ("version", -1),
            ],
            unique=True,
        )
        await self.db["proposal_snapshots"].create_index(
            [
                ("session_id", 1),
                ("proposal_id", 1),
                ("version", -1),
            ],
            unique=True,
        )
        await self.db["proposal_approvals"].create_index(
            [
                ("session_id", 1),
                ("proposal_id", 1),
                ("requested_at", -1),
            ]
        )
        await self.db["horizon_evaluations"].create_index(
            [
                ("session_id", 1),
                ("proposal_id", 1),
                ("created_at", -1),
            ]
        )

    async def register_source_version(
        self,
        *,
        session_id: str,
        proposal_id: str,
        source_id: str,
        content: str,
        title: str,
        created_by: str,
        identifier: str | None = None,
        authority: str = "unverified",
        metadata: dict[str, Any] | None = None,
    ) -> SourceVersionRecord:
        """Register the source version.

        Args:
            session_id (str): Session scope the record belongs to.
            proposal_id (str): The proposal id.
            source_id (str): The source id.
            content (str): Content value.
            title (str): The title.
            created_by (str): The created by.
            identifier (str | None): The identifier (optional, default None).
            authority (str): The authority (optional, default 'unverified').
            metadata (dict[str, Any] | None): Metadata mapping (optional, default None).

        Returns:
            SourceVersionRecord: The source version.
        """
        if not content.strip():
            raise ValueError("source content cannot be empty")
        metadata = metadata or {}
        content_bytes = content.encode("utf-8")
        content_sha = hashlib.sha256(content_bytes).hexdigest()
        metadata_sha = _canonical_hash(
            {
                "title": title,
                "identifier": identifier,
                "authority": authority,
                "metadata": metadata,
            }
        )
        collection = self.db["proposal_source_versions"]
        latest = await collection.find_one(
            {
                "session_id": session_id,
                "proposal_id": proposal_id,
                "source_id": source_id,
            },
            sort=[("version", -1)],
        )
        if (
            latest
            and latest.get("content_sha256") == content_sha
            and latest.get("metadata_sha256") == metadata_sha
        ):
            latest.pop("_id", None)
            return SourceVersionRecord(**latest)

        version = int((latest or {}).get("version") or 0) + 1
        version_id = f"{source_id}:v{version}:{content_sha[:12]}"
        object_key = (
            f"proposal-sources/{session_id}/{proposal_id}/{source_id}/"
            f"{content_sha}.txt"
        )
        await asyncio.to_thread(
            self.object_store.put_bytes,
            content_bytes,
            object_key,
            "text/plain; charset=utf-8",
        )
        record = SourceVersionRecord(
            proposal_id=proposal_id,
            source_id=source_id,
            version=version,
            version_id=version_id,
            content_sha256=content_sha,
            metadata_sha256=metadata_sha,
            object_key=object_key,
            title=title,
            identifier=identifier,
            authority=authority,
            metadata=metadata,
            created_at=_now(),
            created_by=created_by,
        )
        await collection.insert_one(
            {"session_id": session_id, **record.model_dump()}
        )
        return record

    async def list_source_versions(
        self,
        *,
        session_id: str,
        proposal_id: str,
        source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List the source versions.

        Args:
            session_id (str): Session scope the record belongs to.
            proposal_id (str): The proposal id.
            source_id (str | None): The source id (optional, default None).

        Returns:
            list[dict[str, Any]]: The source versions.
        """
        query: dict[str, Any] = {
            "session_id": session_id,
            "proposal_id": proposal_id,
        }
        if source_id:
            query["source_id"] = source_id
        cursor = self.db["proposal_source_versions"].find(
            query,
            {"_id": 0, "session_id": 0},
        ).sort([("source_id", 1), ("version", -1)])
        return [item async for item in cursor]

    async def source_text(
        self,
        *,
        session_id: str,
        proposal_id: str,
        source_id: str,
        version_id: str | None = None,
    ) -> tuple[SourceVersionRecord, str]:
        """Compute the source text.

        Args:
            session_id (str): Session scope the record belongs to.
            proposal_id (str): The proposal id.
            source_id (str): The source id.
            version_id (str | None): Version identifier (optional, default None).

        Returns:
            tuple[SourceVersionRecord, str]: The text.
        """
        query: dict[str, Any] = {
            "session_id": session_id,
            "proposal_id": proposal_id,
            "source_id": source_id,
        }
        if version_id:
            query["version_id"] = version_id
        record = await self.db["proposal_source_versions"].find_one(
            query,
            sort=None if version_id else [("version", -1)],
        )
        if record is None:
            raise KeyError(f"source version not found: {source_id}")
        raw = await asyncio.to_thread(
            self.object_store.get_bytes,
            record["object_key"],
        )
        record.pop("_id", None)
        record.pop("session_id", None)
        return SourceVersionRecord(**record), raw.decode(
            "utf-8",
            errors="replace",
        )

    async def save_snapshot(
        self,
        *,
        session_id: str,
        proposal_id: str,
        graph: ProposalGraph,
        created_by: str,
        reason: str,
    ) -> ProposalSnapshot:
        """Save the snapshot.

        Args:
            session_id (str): Session scope the record belongs to.
            proposal_id (str): The proposal id.
            graph (ProposalGraph): Compiled LangGraph graph.
            created_by (str): The created by.
            reason (str): Reason text.

        Returns:
            ProposalSnapshot: The snapshot.
        """
        graph_payload = graph.model_dump(mode="json")
        content_sha = _canonical_hash(graph_payload)
        collection = self.db["proposal_snapshots"]
        latest = await collection.find_one(
            {"session_id": session_id, "proposal_id": proposal_id},
            sort=[("version", -1)],
        )
        if latest and latest.get("content_sha256") == content_sha:
            latest.pop("_id", None)
            latest.pop("session_id", None)
            return ProposalSnapshot(**latest)

        version = int((latest or {}).get("version") or 0) + 1
        snapshot = ProposalSnapshot(
            proposal_id=proposal_id,
            version=version,
            snapshot_id=f"PS-{proposal_id}-v{version}-{content_sha[:10]}",
            content_sha256=content_sha,
            graph=graph_payload,
            created_at=_now(),
            created_by=created_by,
            reason=reason,
        )
        await collection.insert_one(
            {"session_id": session_id, **snapshot.model_dump()}
        )
        return snapshot

    async def request_approval(
        self,
        *,
        session_id: str,
        proposal_id: str,
        graph: ProposalGraph,
        stage: str,
        requested_by: str,
        selected_concept_id: str | None = None,
    ) -> ApprovalRecord:
        """Compute the request approval.

        Args:
            session_id (str): Session scope the record belongs to.
            proposal_id (str): The proposal id.
            graph (ProposalGraph): Compiled LangGraph graph.
            stage (str): Pipeline stage label.
            requested_by (str): The requested by.
            selected_concept_id (str | None): The selected concept id (optional, default None).

        Returns:
            ApprovalRecord: The approval.
        """
        coverage = build_call_coverage_matrix(graph)
        if coverage.submission_blocked and stage in {
            "call_coverage",
            "concept_freeze",
            "final_submission",
        }:
            raise ValueError(
                "approval is blocked by uncovered mandatory requirements: "
                + ", ".join(coverage.blocking_requirement_ids)
            )
        if stage == "concept_freeze":
            if (
                not selected_concept_id
                or selected_concept_id not in graph.concept_alternatives
            ):
                raise ValueError(
                    "concept_freeze requires a selected concept alternative"
                )

        snapshot = await self.save_snapshot(
            session_id=session_id,
            proposal_id=proposal_id,
            graph=graph,
            created_by=requested_by,
            reason=f"approval_request:{stage}",
        )
        approval = ApprovalRecord(
            approval_id="APR-" + uuid.uuid4().hex[:16],
            proposal_id=proposal_id,
            stage=stage,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            snapshot_sha256=snapshot.content_sha256,
            status="pending",
            selected_concept_id=selected_concept_id,
            coverage=coverage,
            requested_by=requested_by,
            requested_at=_now(),
        )
        await self.db["proposal_approvals"].insert_one(
            {"session_id": session_id, **approval.model_dump()}
        )
        return approval

    async def decide_approval(
        self,
        *,
        session_id: str,
        proposal_id: str,
        approval_id: str,
        decision: Literal["approved", "rejected", "changes_requested"],
        decided_by: str,
        comment: str | None,
    ) -> ApprovalRecord:
        """Compute the decide approval.

        Args:
            session_id (str): Session scope the record belongs to.
            proposal_id (str): The proposal id.
            approval_id (str): The approval id.
            decision (Literal['approved', 'rejected', 'changes_requested']): Human decision mapping.
            decided_by (str): The decided by.
            comment (str | None): The comment.

        Returns:
            ApprovalRecord: The approval.
        """
        collection = self.db["proposal_approvals"]
        updated = await collection.find_one_and_update(
            {
                "session_id": session_id,
                "proposal_id": proposal_id,
                "approval_id": approval_id,
                "status": "pending",
            },
            {
                "$set": {
                    "status": decision,
                    "decided_by": decided_by,
                    "decided_at": _now(),
                    "comment": comment,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise KeyError(
                "approval not found, not pending, or outside this session"
            )
        updated.pop("_id", None)
        updated.pop("session_id", None)
        return ApprovalRecord(**updated)

    async def list_approvals(
        self,
        *,
        session_id: str,
        proposal_id: str,
    ) -> list[dict[str, Any]]:
        """List the approvals.

        Args:
            session_id (str): Session scope the record belongs to.
            proposal_id (str): The proposal id.

        Returns:
            list[dict[str, Any]]: The approvals.
        """
        cursor = self.db["proposal_approvals"].find(
            {"session_id": session_id, "proposal_id": proposal_id},
            {"_id": 0, "session_id": 0},
        ).sort("requested_at", -1)
        return [item async for item in cursor]
