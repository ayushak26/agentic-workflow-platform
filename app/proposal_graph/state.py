"""Workflow-state adapter for the EU proposal use-case pack."""
from __future__ import annotations

from typing import Any

from app.proposal_graph import PROPOSAL_NAMESPACE
from app.proposal_graph.graph import ProposalGraph


def proposal_graph_from_state(state: dict[str, Any]) -> ProposalGraph:
    domain_state = state.get("domain_state") or {}
    raw = domain_state.get(PROPOSAL_NAMESPACE) or {}
    return raw if isinstance(raw, ProposalGraph) else ProposalGraph(**raw)


def proposal_graph_state_update(graph: ProposalGraph) -> dict[str, Any]:
    return {
        "domain_state": {
            PROPOSAL_NAMESPACE: graph.model_dump(),
        }
    }