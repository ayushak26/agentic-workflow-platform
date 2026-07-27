"""Shared mutable state that flows through every LangGraph node.

The reducers on Annotated fields are what make parallel branches safe.
Without them, concurrent writes raise InvalidUpdateError."""
from __future__ import annotations
from typing import Annotated, Any, TypedDict
from operator import add

from app.proposal_graph.graph import ProposalGraph, merge_graph


def merge_node_outputs(
    a: dict[str, Any], b: dict[str, Any]
) -> dict[str, Any]:
    """Reducer for node_outputs.

    When five Section Drafters write concurrently, LangGraph calls this
    reducer to merge their writes. Each writer writes {node_id: output},
    so a dict union is correct and side-effect-free."""
    merged = dict(a)
    merged.update(b)
    return merged


class WorkflowState(TypedDict, total=False):
    # Inputs supplied at invocation (the RFP PDF reference, client context, etc.)
    inputs: dict[str, Any]

    # Per-node outputs. Reducer lets parallel branches all write here.
    node_outputs: Annotated[dict[str, Any], merge_node_outputs]

    # Append-only audit log. 'add' on lists = concatenation.
    audit_log: Annotated[list[dict], add]

    # Session isolation — every workflow run is keyed by this; retrieval,
    # cache, and Weaviate filter on it. The proposal's 'Pigeon Holes' requirement.
    session_id: str

    # Collection scope — which logical corpus this run reads from. Dual role:
    # AND-ed into the Weaviate filter (corpus isolation, like session_id) AND
    # the key that loads the controlled vocabulary for doc_type validation.
    collection_id: str

    # Typed Proposal Knowledge Graph — the single source of truth a proposal is
    # written FROM. Additive: nodes that don't touch it are unaffected (total=False).
    # Its reducer merges parallel drafter writes field-by-field so two drafters can
    # fill different fields of the same object without clobbering each other —
    # the same parallel-branch safety merge_node_outputs gives, but lossless at
    # the field level (a dict.update-style reducer would let a partial write erase
    # a fuller one). ConsistencyChecker reads this to gate the render.
    proposal_graph: Annotated[dict, merge_graph]

    # Workflow metadata
    workflow_id: str
    workflow_name: str