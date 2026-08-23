"""Proposal workspace graph: domain state and workspace store."""
from app.runtime.domain_state import DomainStateRegistry

from app.proposal_graph.graph import ProposalGraph, empty_graph, merge_graph

PROPOSAL_NAMESPACE = "eu_proposal"

# Importing the proposal pack registers only its own merge semantics. The core
# runtime remains unaware of ProposalGraph and can run without this package.
DomainStateRegistry.register(PROPOSAL_NAMESPACE, merge_graph)

__all__ = [
    "PROPOSAL_NAMESPACE",
    "ProposalGraph",
    "empty_graph",
    "merge_graph",
]