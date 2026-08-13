"""Editable Retrieval Playground starting configurations.

Choosing one only pre-fills the Playground's editable knobs — nothing is
saved until the caller explicitly saves an experiment as a Retrieval
Profile, via ``POST /api/knowledge/profiles``.
"""
from __future__ import annotations

from typing import Any

RETRIEVAL_PRESETS: dict[str, dict[str, Any]] = {
    "fast": {
        "name": "Fast",
        "strategy": "dense",
        "config": {
            "strategy": "dense",
            "candidate_count": 10,
            "final_count": 4,
            "fusion_strategy": "relative_score",
            "reranking_enabled": False,
            "compression_enabled": False,
            "query_transform": "none",
            "context_expansion": "none",
        },
    },
    "balanced": {
        "name": "Balanced",
        "strategy": "hybrid_rerank",
        "config": {
            "strategy": "hybrid_rerank",
            "candidate_count": 20,
            "final_count": 6,
            "alpha": 0.5,
            "fusion_strategy": "relative_score",
            "reranking_enabled": True,
            "compression_enabled": True,
            "query_transform": "none",
            "context_expansion": "none",
        },
    },
    "high_recall": {
        "name": "High Recall",
        "strategy": "hybrid_rerank",
        "config": {
            "strategy": "hybrid_rerank",
            "candidate_count": 50,
            "final_count": 12,
            "alpha": 0.5,
            "fusion_strategy": "rrf",
            "reranking_enabled": True,
            "compression_enabled": False,
            "query_transform": "multi_query",
            "context_expansion": "none",
        },
    },
    "technical_documentation": {
        "name": "Technical Documentation",
        "strategy": "hybrid_rerank",
        "config": {
            "strategy": "hybrid_rerank",
            "candidate_count": 24,
            "final_count": 8,
            "alpha": 0.5,
            "fusion_strategy": "relative_score",
            "reranking_enabled": True,
            "compression_enabled": True,
            "query_transform": "none",
            "context_expansion": "parent",
        },
    },
    "research": {
        "name": "Research",
        "strategy": "hybrid_rerank",
        "config": {
            "strategy": "hybrid_rerank",
            "candidate_count": 40,
            "final_count": 10,
            "alpha": 0.5,
            "fusion_strategy": "rrf",
            "reranking_enabled": True,
            "compression_enabled": True,
            "query_transform": "decomposition",
            "context_expansion": "contextual",
        },
    },
}
