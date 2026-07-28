"""Shared mutable state that flows through every LangGraph node.

The reducers on Annotated fields are what make parallel branches safe.
Without them, concurrent writes raise InvalidUpdateError."""
from __future__ import annotations
from typing import Annotated, Any, TypedDict
from operator import add

from .domain_state import merge_domain_state


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

    # Provider-neutral model choices emitted by the gateway. This contains no
    # prompts or generated content and remains safe for operator-visible UI.
    model_selections: Annotated[list[dict], add]

    # Session isolation — every workflow run is keyed by this; retrieval,
    # cache, and Weaviate filter on it. The proposal's 'Pigeon Holes' requirement.
    session_id: str

    # Collection scope — which logical corpus this run reads from. Dual role:
    # AND-ed into the Weaviate filter (corpus isolation, like session_id) AND
    # the key that loads the controlled vocabulary for doc_type validation.
    collection_id: str

    # Workflow-owned constants. They are separate from user inputs so callers
    # cannot silently override a policy or pack setting.
    variables: dict[str, Any]

    # Optional use-case packs write typed state under their own namespace.
    # Examples: domain_state["eu_proposal"], ["prior_authorization"], ["sales"].
    # The runtime owns only the namespace boundary; each pack owns its reducer.
    domain_state: Annotated[dict[str, Any], merge_domain_state]

    # Workflow metadata
    workflow_id: str
    workflow_name: str
