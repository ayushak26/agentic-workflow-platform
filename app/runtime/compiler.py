"""Compile a WorkflowSpec into a runnable LangGraph StateGraph.

Responsibilities:
1. Instantiate every node from the registry (validates config schemas).
2. Wrap each node in a runtime function that resolves templates, calls run(),
   and merges output into state.
3. Lay out edges: simple, fan-out, and conditional (router) edges.
4. Wire START and END.
5. Return a compiled graph with a checkpointer attached.

Cost tracking (scope changes):
_make_runtime_fn now receives the services dict and binds a context-aware
gateway clone inside runtime_fn at call time — not at compile time, because
run_id doesn't exist until the workflow is invoked.
"""
from __future__ import annotations
import time
from typing import Any
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.nodes.registry import NodeRegistry
from app.observability import metrics
from .schema import WorkflowSpec, EdgeSpec
from .state import WorkflowState
from .templating import resolve
from .events import RunEvent, RunEventBus
from .node_events import sanitize_preview, is_graph_interrupt


def _make_runtime_fn(instance, bus: RunEventBus | None, services: dict):
    """Wrap a NodeType instance so it conforms to LangGraph's
    'state -> partial state' contract.

    Cost tracking: gateway context is bound at call time inside runtime_fn.
    with_context() returns a shallow clone of the singleton — parallel
    branches each get isolated context, no locking needed.
    """
    node_id = instance.node_id
    type_name = instance.type_name

    async def runtime_fn(state: dict) -> dict:
        run_id = state.get("inputs", {}).get("SYSTEM.run_id")
        session_id = state.get("session_id", "unknown")
        started = time.time()

        # Bind cost-tracking context for this specific node call.
        llm = services.get("llm")
        if llm is not None and hasattr(llm, "with_context"):
            node_services = {
            **services,
            "llm": llm.with_context(
                run_id=run_id or "unknown",
                session_id=session_id,
                node_id=node_id,
                ledger=services.get("cost_ledger"),
            ),
        }
        else:
            node_services = services  # no gateway — use services as-is

        instance.services = node_services

        if bus and run_id:
            await bus.publish(RunEvent(
                type="node_started",
                run_id=run_id,
                node_id=node_id,
            ))

        try:
            with metrics.track_node(type_name):
                resolved = resolve(instance.config.model_dump(), state)
                output = await instance.run(state, resolved)
                instance.output_schema(**output)
        except BaseException as e:
            if bus and run_id:
                if is_graph_interrupt(e):
                    await bus.publish(RunEvent(
                        type="node_paused",
                        run_id=run_id,
                        node_id=node_id,
                        context={"interrupt": sanitize_preview(getattr(e, "args", None))},
                    ))
                else:
                    await bus.publish(RunEvent(
                        type="run_failed",
                        run_id=run_id,
                        node_id=node_id,
                        error=str(e)[:240],
                    ))
            raise

        if bus and run_id:
            await bus.publish(RunEvent(
                type="node_completed",
                run_id=run_id,
                node_id=node_id,
                output_preview=sanitize_preview(output),
            ))

        return {
            "node_outputs": {node_id: output},
            "audit_log": [{
                "node_id": node_id,
                "type_name": type_name,
                "started_at": started,
                "duration_s": time.time() - started,
                "output_keys": list(output.keys()),
            }],
        }

    runtime_fn.__name__ = f"node_{node_id}"
    return runtime_fn


def _wire_edges(
    graph: StateGraph, edges: list[EdgeSpec], hitl_ids: set[str]
) -> set[str]:
    """Add edges, return the set of source node ids (used to compute terminals)."""
    sources: set[str] = set()
    for edge in edges:
        sources.add(edge.from_)
        if edge.condition and edge.branches:
            def _router(state: dict, _edge=edge) -> str:
                decision = state["node_outputs"][_edge.from_].get("route")
                if decision not in _edge.branches:
                    raise ValueError(
                        f"Router {_edge.from_} returned unknown route {decision!r}; "
                        f"expected one of {list(_edge.branches)}"
                    )
                return decision

            graph.add_conditional_edges(edge.from_, _router, edge.branches)

        elif edge.from_ in hitl_ids:
            targets = edge.to if isinstance(edge.to, list) else [edge.to]

            def _hitl_router(state: dict, _edge=edge):
                decision = (
                    state.get("node_outputs", {})
                    .get(_edge.from_, {})
                    .get("decision")
                )
                if decision == "reject":
                    return END
                return _edge.to if isinstance(_edge.to, list) else _edge.to

            graph.add_conditional_edges(edge.from_, _hitl_router, [*targets, END])

        elif isinstance(edge.to, list):
            for target in edge.to:
                graph.add_edge(edge.from_, target)
        elif edge.to:
            graph.add_edge(edge.from_, edge.to)

    return sources


def compile_workflow(spec: WorkflowSpec, checkpointer=None, services=None):
    graph = StateGraph(WorkflowState)
    services = services or {}
    bus: RunEventBus | None = services.get("event_bus")

    # 1. Instantiate nodes — config schemas validated here at compile time.
    instances = {}
    for node_spec in spec.nodes:
        node_class = NodeRegistry.get(node_spec.type)
        instances[node_spec.id] = node_class(
            node_spec.id, node_spec.config, services=services
        )

    # Which nodes are human-in-loop? Their edges route reject → END.
    hitl_ids = {
        nid for nid, inst in instances.items()
        if inst.type_name == "HumanInLoopAgent"
    }

    # 2. Add each node as a runtime function — bus-aware and cost-aware.
    for node_id, instance in instances.items():
        graph.add_node(node_id, _make_runtime_fn(instance, bus, services))

    # 3-5. Wire edges, entry, and exits.
    sources = _wire_edges(graph, spec.edges, hitl_ids)

    entry = spec.entry or spec.nodes[0].id
    graph.add_edge(START, entry)

    if spec.exit:
        exits = [spec.exit] if isinstance(spec.exit, str) else spec.exit
    else:
        all_ids = {n.id for n in spec.nodes}
        exits = list(all_ids - sources)

    for exit_id in exits:
        graph.add_edge(exit_id, END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())