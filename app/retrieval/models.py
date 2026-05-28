"""Pydantic models for the retrieval pipeline.

These are the only types the outside world (RAG Agent node, API, Cockpit,
audit log) ever sees. Weaviate-specific types stay inside hybrid_search.py.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---- Enums --------------------------------------------------------------

DocType = Literal[
    "proposal",
    "case_study",
    "methodology",
    "playbook",
    "report",
]


# ---- Input side ---------------------------------------------------------

class RetrievalFilters(BaseModel):
    """Metadata pre-filter applied BEFORE hybrid search runs in Weaviate.

    session_id is mandatory: it is the security boundary enforced at the
    retrieval layer (see docs/security.md and Phase 11 isolation verifier).
    Every chunk in Weaviate carries a session_id; every query pins one.
    """

    session_id: str = Field(..., min_length=1)
    industry: Optional[str] = None
    doc_types: Optional[list[DocType]] = None
    date_after: Optional[date] = None
    date_before: Optional[date] = None


class RetrievalQuery(BaseModel):
    """Single retrieval request. Defaults come from app.config."""

    query: str = Field(..., min_length=1)
    filters: RetrievalFilters

    # Pipeline knobs — all defaults match app/config.py.
    top_k_candidates: int = Field(default=25, ge=5, le=100)
    top_n_final: int = Field(default=8, ge=1, le=20)
    alpha: float = Field(default=0.5, ge=0.0, le=1.0)  # 0=BM25, 1=vector

    # Stage toggles — let callers (and the eval lab) ablate stages.
    rewrite_query: bool = True
    rerank: bool = True
    compress: bool = True


# ---- Output side --------------------------------------------------------

class RetrievedChunk(BaseModel):
    """One chunk that survived the pipeline.

    Score fields are progressively filled in:
      stage 2 (hybrid_search)  -> hybrid_score
      stage 3 (reranker)       -> rerank_score, rerank_reason
      stage 4 (compressor)     -> compressed_text
    """

    chunk_id: str
    doc_id: str
    doc_title: str
    doc_type: DocType
    text: str                              # raw chunk from Weaviate
    metadata: dict                         # industry, doc_date, source_uri, etc.

    hybrid_score: float
    rerank_score: Optional[float] = None
    rerank_reason: Optional[str] = None    # shown in the Cockpit
    compressed_text: Optional[str] = None  # what the drafter actually sees


class RetrievalResult(BaseModel):
    """Result of one retrieve() call. This is what the RAG Agent node returns
    in the workflow state and what the Cockpit displays in the retrievals
    panel."""

    query: str
    rewritten_query: Optional[str]
    chunks: list[RetrievedChunk]
    filters_applied: RetrievalFilters
    timings_ms: dict[str, float]           # per-stage latency, e.g.
                                           # {"hybrid_search_ms": 184.2,
                                           #  "rerank_ms": 920.5,
                                           #  "compress_ms": 410.1}