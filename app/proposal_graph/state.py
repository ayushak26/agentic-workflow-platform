"""Workflow-state adapter for the EU proposal use-case pack."""
from __future__ import annotations

from typing import Any

from app.proposal_graph import PROPOSAL_NAMESPACE
from app.proposal_graph.graph import ProposalGraph


def proposal_graph_from_state(state: dict[str, Any]) -> ProposalGraph:
    """Compute the proposal graph from state.

    Args:
        state (dict[str, Any]): Current workflow state.

    Returns:
        ProposalGraph: The graph from state.
    """
    domain_state = state.get("domain_state") or {}
    raw = domain_state.get(PROPOSAL_NAMESPACE) or {}
    return raw if isinstance(raw, ProposalGraph) else ProposalGraph(**raw)


def proposal_graph_state_update(graph: ProposalGraph) -> dict[str, Any]:
    """Compute the proposal graph state update.

    Args:
        graph (ProposalGraph): Compiled LangGraph graph.

    Returns:
        dict[str, Any]: The graph state update.
    """
    return {
        "domain_state": {
            PROPOSAL_NAMESPACE: graph.model_dump(),
        }
    }