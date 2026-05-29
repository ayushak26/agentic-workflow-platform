"""Shared mutable state that flows through every LangGraph node.

The reducers on Annotated fields are what make parallel branches safe.
Without them, concurrent writes raise InvalidUpdateError."""
from __future__ import annotations
from typing import Annotated, Any, TypedDict
from operator import add


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

    # Workflow metadata
    workflow_id: str
    workflow_name: str