"""Compile a WorkflowSpec into a runnable LangGraph StateGraph.

Responsibilities:
1. Instantiate every node from the registry (validates config schemas).
2. Wrap each node in a runtime function that resolves templates, calls run(),
   and merges output into state.
3. Lay out edges: simple, fan-out, and conditional (router) edges.
4. Wire START and END.
5. Return a compiled graph with a checkpointer attached.
"""
from __future__ import annotations
import time
from typing import Any
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.nodes.registry import NodeRegistry
from .schema import WorkflowSpec, EdgeSpec
from .state import WorkflowState
from .templating import resolve


def _make_runtime_fn(instance):
    """Wrap a NodeType instance so it conforms to LangGraph's
    'state -> partial state' contract."""
    async def runtime_fn(state: dict) -> dict:
        started = time.time()
        # Resolve templates in this node's config against current state
        resolved = resolve(instance.config.model_dump(), state)
        # Execute the node
        output = await instance.run(state, resolved)
        # Validate against the declared output_schema (defensive)
        instance.output_schema(**output)
        # Return partial state — LangGraph applies reducers automatically
        return {
            "node_outputs": {instance.node_id: output},
            "audit_log": [{
                "node_id": instance.node_id,
                "type_name": instance.type_name,
                "started_at": started,
                "duration_s": time.time() - started,
                "output_keys": list(output.keys()),
            }],
        }
    runtime_fn.__name__ = f"node_{instance.node_id}"
    return runtime_fn


def _wire_edges(graph: StateGraph, edges: list[EdgeSpec]) -> set[str]:
    """Add edges, return the set of 'source' node ids (used to compute terminals)."""
    sources: set[str] = set()
    for edge in edges:
        sources.add(edge.from_)
        if edge.condition and edge.branches:
            # Conditional router edge — Phase 5 implements RouterAgent that
            # writes the routing decision into its output; we read it here.
            def _router(state: dict, _edge=edge) -> str:
                # Convention: routing decision lives at node_outputs[from_]['route']
                decision = state["node_outputs"][_edge.from_].get("route")
                target = _edge.branches.get(decision)
                if target is None:
                    raise ValueError(
                        f"Router {_edge.from_} returned unknown route {decision!r}"
                    )
                return target
            graph.add_conditional_edges(edge.from_, _router, edge.branches)
        elif isinstance(edge.to, list):
            # Fan-out: from one node to many. LangGraph will run them in parallel.
            for target in edge.to:
                graph.add_edge(edge.from_, target)
        elif edge.to:
            graph.add_edge(edge.from_, edge.to)
    return sources


def compile_workflow(spec: WorkflowSpec, checkpointer=None, services=None):
    graph = StateGraph(WorkflowState)
    services = services or {}  

    # 1. Instantiate nodes (this validates every node's config against its schema)
    instances = {}
    for node_spec in spec.nodes:
        node_class = NodeRegistry.get(node_spec.type)
        instances[node_spec.id] = node_class(node_spec.id, node_spec.config, services=services)

    # 2. Add each node as a runtime function
    for node_id, instance in instances.items():
        graph.add_node(node_id, _make_runtime_fn(instance))

    # 3. Wire edges
    sources = _wire_edges(graph, spec.edges)

    # 4. Entry and exit
    entry = spec.entry or spec.nodes[0].id
    graph.add_edge(START, entry)

    if spec.exit:
        exits = [spec.exit] if isinstance(spec.exit, str) else spec.exit
    else:
        # Terminal nodes = nodes that are never an edge source
        all_ids = {n.id for n in spec.nodes}
        exits = list(all_ids - sources)
    for exit_id in exits:
        graph.add_edge(exit_id, END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())