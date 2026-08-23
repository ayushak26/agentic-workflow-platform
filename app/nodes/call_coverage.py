"""Workflow node that computes and writes the deterministic call matrix."""
from __future__ import annotations

from pydantic import BaseModel

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.coverage import (
    CallCoverageMatrix,
    build_call_coverage_matrix,
)
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.state import (
    proposal_graph_from_state,
    proposal_graph_state_update,
)


class CallCoverageInput(BaseModel):
    """Pydantic model defining the CallCoverageInput shape."""
    pass


class CallCoverageConfig(BaseModel):
    """Pydantic model defining the CallCoverageConfig shape."""
    pass


@NodeRegistry.register
class CallCoverageMatrixAgent(NodeType):
    """Workflow node type implementing the CallCoverageMatrixAgent capability."""
    type_name = "CallCoverageMatrixAgent"
    description = (
        "Build a deterministic requirement-by-requirement Horizon call "
        "coverage matrix and submission gate."
    )
    input_schema = CallCoverageInput
    config_schema = CallCoverageConfig
    output_schema = CallCoverageMatrix

    async def run(self, state: dict, resolved_config: dict) -> dict:
        """Run the result.

        Args:
            state (dict): Current workflow state.
            resolved_config (dict): Configuration after template resolution.

        Returns:
            dict: The result.
        """
        graph = proposal_graph_from_state(state)
        matrix = build_call_coverage_matrix(graph)
        updated = {
            row.requirement_id: graph.call_requirements[
                row.requirement_id
            ].model_copy(update={"coverage": row.status})
            for row in matrix.rows
        }
        payload = matrix.model_dump()
        payload["__state__"] = proposal_graph_state_update(
            ProposalGraph(call_requirements=updated)
        )
        return payload
