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

Audit logging (Phase 11A):
runtime_fn writes durable, session-scoped audit rows to Mongo via
services["audit_db"] — separate from the in-state `audit_log` breadcrumb,
which stays for debugging. node_error is written ONLY on real failures, not
on HITL interrupts, reusing the existing is_graph_interrupt() split.
"""
from __future__ import annotations
from copy import deepcopy
import time
from typing import Any
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.nodes.registry import NodeRegistry
from app.observability import metrics
from app.observability.logging import get_logger
from app.security.audit import (
    write_audit_event,
    summarize_payload,
    NODE_START,
    NODE_END,
    NODE_REUSED,
    NODE_ERROR,
)
from app.workflow.run_history import (
    build_node_input,
    record_checkpoint_node_completed,
    record_checkpoint_paused,
    record_node_completed,
    record_node_failed,
    record_node_paused,
    record_node_reused,
    record_node_started,
)
from .schema import WorkflowSpec, EdgeSpec
from .state import WorkflowState
from .templating import resolve
from .events import RunEvent, RunEventBus
from .node_events import sanitize_preview, is_graph_interrupt

log = get_logger(__name__)


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

        # Durable audit sink (Mongo handle), threaded via services like cost_ledger.
        audit = services.get("audit_db")

        # A retry replays exact outputs/state deltas from completed nodes. This
        # branch returns before a provider gateway is bound or called, so reused
        # LLM nodes consume zero new tokens.
        reused_results = services.get("reused_node_results") or {}
        cached_result = reused_results.get(node_id)
        if cached_result is not None:
            output = deepcopy(cached_result.get("output") or {})
            extra_state = deepcopy(cached_result.get("extra_state") or {})
            instance.output_schema(**output)
            ended = time.time()
            source_run_id = str(
                services.get("retry_source_run_id") or "unknown"
            )

            replay_existing = bool(services.get("resume_replay"))
            if bus and run_id and not replay_existing:
                await bus.publish(
                    RunEvent(
                        type="node_reused",
                        run_id=run_id,
                        session_id=session_id,
                        node_id=node_id,
                        output_preview=sanitize_preview(output),
                    )
                )
            if audit is not None and run_id and not replay_existing:
                await write_audit_event(
                    audit,
                    run_id,
                    session_id,
                    node_id,
                    NODE_REUSED,
                    payload={"source_run_id": source_run_id},
                )
                await record_node_reused(
                    audit,
                    run_id=run_id,
                    session_id=session_id,
                    node_id=node_id,
                    type_name=type_name,
                    output=output,
                    source_run_id=source_run_id,
                    ended_at=ended,
                    duration_s=ended - started,
                )
                await record_checkpoint_node_completed(
                    audit,
                    run_id=run_id,
                    session_id=session_id,
                    node_id=node_id,
                    output=output,
                    extra_state=extra_state,
                )

            state_update = {
                "audit_log": [
                    {
                        "node_id": node_id,
                        "type_name": type_name,
                        "started_at": started,
                        "duration_s": ended - started,
                        "output_keys": list(output.keys()),
                        "reused": True,
                        "source_run_id": source_run_id,
                    }
                ],
                **extra_state,
            }
            patched_outputs = dict(extra_state.get("node_outputs") or {})
            patched_outputs[node_id] = output
            state_update["node_outputs"] = patched_outputs
            return state_update

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
                    event_bus=bus,
                    node_type=type_name,
                    allowed_models=getattr(instance, "_allowed_models", None),
                    routing_policy=getattr(instance, "_model_routing", None),
                ),
            }
        else:
            node_services = services

        instance.services = node_services

        if bus and run_id:
            await bus.publish(RunEvent(
                type="node_started",
                run_id=run_id,
                session_id=session_id,
                node_id=node_id,
            ))
        if audit is not None and run_id:
            await write_audit_event(audit, run_id, session_id, node_id, NODE_START)

        try:
            with metrics.track_node(type_name):
                resolved = resolve(instance.config.model_dump(), state)
                if audit is not None and run_id:
                    await record_node_started(
                        audit,
                        run_id=run_id,
                        session_id=session_id,
                        node_id=node_id,
                        type_name=type_name,
                        node_input=build_node_input(state, resolved),
                        started_at=started,
                    )
                output = await instance.run(state, resolved)
                extra_state = output.pop("__state__", {}) if isinstance(output, dict) else {}
                instance.output_schema(**output)
        except BaseException as e:
            # Log to stdout FIRST, before any client emission. A failure that is
            # only published to the SSE bus vanishes the moment the client
            # disconnects (the run then returns 200 with no server-side trace).
            # Server-side logging must not depend on a client being connected.
            # Interrupts are control flow (HITL pause), not errors — info only.
            if is_graph_interrupt(e):
                log.info("node_paused", node_id=node_id, type_name=type_name,
                         run_id=run_id)
                if audit is not None and run_id:
                    await record_node_paused(
                        audit,
                        run_id=run_id,
                        session_id=session_id,
                        node_id=node_id,
                        paused_at=time.time(),
                    )
                    await record_checkpoint_paused(
                        audit,
                        run_id=run_id,
                        session_id=session_id,
                        node_id=node_id,
                        pause_context={
                            "interrupt": sanitize_preview(
                                getattr(e, "args", None)
                            )
                        },
                    )
            else:
                log.error("node_failed", node_id=node_id, type_name=type_name,
                          run_id=run_id, error=str(e), exc_info=True)
                # Audit only real failures — an interrupt is a HITL pause, not an error.
                if audit is not None and run_id:
                    await write_audit_event(
                        audit, run_id, session_id, node_id, NODE_ERROR,
                        payload={"error": str(e)[:200]},
                    )
                    await record_node_failed(
                        audit,
                        run_id=run_id,
                        session_id=session_id,
                        node_id=node_id,
                        type_name=type_name,
                        error=str(e)[:500],
                        ended_at=time.time(),
                        duration_s=time.time() - started,
                    )
            if bus and run_id:
                if is_graph_interrupt(e):
                    await bus.publish(RunEvent(
                        type="node_paused",
                        run_id=run_id,
                        session_id=session_id,
                        node_id=node_id,
                        context={"interrupt": sanitize_preview(getattr(e, "args", None))},
                    ))
                else:
                    await bus.publish(RunEvent(
                        type="run_failed",
                        run_id=run_id,
                        session_id=session_id,
                        node_id=node_id,
                        error=str(e)[:240],
                    ))
            raise

        if bus and run_id:
            await bus.publish(RunEvent(
                type="node_completed",
                run_id=run_id,
                session_id=session_id,
                node_id=node_id,
                output_preview=sanitize_preview(output),
            ))
        if audit is not None and run_id:
            await write_audit_event(
                audit, run_id, session_id, node_id, NODE_END,
                payload=summarize_payload(output),   # shape only, never content
            )
            await record_checkpoint_node_completed(
                audit,
                run_id=run_id,
                session_id=session_id,
                node_id=node_id,
                output=output,
                extra_state=extra_state,
            )
            await record_node_completed(
                audit,
                run_id=run_id,
                session_id=session_id,
                node_id=node_id,
                output=output,
                ended_at=time.time(),
                duration_s=time.time() - started,
            )

        state_update = {
            "audit_log": [{
                "node_id": node_id,
                "type_name": type_name,
                "started_at": started,
                "duration_s": time.time() - started,
                "output_keys": list(output.keys()),
            }],
            **extra_state,
        }
        # An HITL edit can patch a previous node through __state__. Preserve
        # both that patch and this node's own decision/output. If extra_state
        # overwrites node_outputs, the approval record disappears.
        patched_outputs = dict(extra_state.get("node_outputs") or {})
        patched_outputs[node_id] = output
        state_update["node_outputs"] = patched_outputs
        bound_llm = node_services.get("llm")
        selections = getattr(bound_llm, "selection_history", None) or []
        if selections:
            # Reducer on WorkflowState.model_selections is `add`, so returning a
            # list appends it across nodes. Only write when non-empty to avoid
            # emitting an empty list every node.
            state_update["model_selections"] = list(selections)
        return state_update
    
    runtime_fn.__name__ = f"node_{node_id}"
    return runtime_fn

def _wire_edges(
    graph: StateGraph, edges: list[EdgeSpec], hitl_ids: set[str]
) -> set[str]:
    """Add edges, return the set of source node ids (used to compute terminals).

    HITL nodes are wired ONCE each (not once per edge): a HITL node may fan out
    to multiple parallel targets (e.g. approve -> 5 drafters). We collect every
    target of each HITL node and register a single conditional router that sends
    all of them on 'approve'/'edit' and END on 'reject'. Registering per-edge
    would call add_conditional_edges twice for the same node -> LangGraph raises
    "Branch with name '_hitl_router' already exists".
    """
    sources: set[str] = set()

    # 1. Collect all targets for each HITL node (across its multiple edges).
    hitl_targets: dict[str, list[str]] = {}
    for edge in edges:
        if edge.from_ in hitl_ids and not (edge.condition and edge.branches):
            tgts = edge.to if isinstance(edge.to, list) else [edge.to]
            hitl_targets.setdefault(edge.from_, []).extend(tgts)

    # 2. Register exactly one conditional router per HITL node.
    for hitl_id, targets in hitl_targets.items():
        uniq = list(dict.fromkeys(targets))  # dedup, preserve order

        def _hitl_router(state: dict, _targets=uniq, _hid=hitl_id):
            decision = (
                state.get("node_outputs", {})
                .get(_hid, {})
                .get("decision")
            )
            if decision == "reject":
                return END
            # approve / edit -> proceed to ALL fan-out targets in parallel
            return _targets

        graph.add_conditional_edges(hitl_id, _hitl_router, [*uniq, END])

    # 3. Wire everything else (routers, plain edges, fan-outs).
    #
    # Plain edges into the SAME target are collected and wired with one
    # add_edge(sources, target) call instead of one add_edge() per source.
    # LangGraph only treats multiple predecessors as an AND-join (wait for
    # ALL of them) when they're passed together as a list in a single call.
    # N separate add_edge(source_i, target) calls instead each act as an
    # independent OR-trigger, so a fan-in node with predecessors at unequal
    # distances (e.g. one 1-hop upstream, another 4 hops upstream) fires as
    # soon as the FIRST of them completes — with the rest of its inputs
    # still missing — instead of waiting for the last one.
    plain_targets: dict[str, list[str]] = {}
    for edge in edges:
        sources.add(edge.from_)

        # HITL nodes already fully wired above — skip their plain edges.
        if edge.from_ in hitl_ids and not (edge.condition and edge.branches):
            continue

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
            continue

        targets = edge.to if isinstance(edge.to, list) else ([edge.to] if edge.to else [])
        for target in targets:
            plain_targets.setdefault(target, []).append(edge.from_)

    for target, srcs in plain_targets.items():
        uniq_srcs = list(dict.fromkeys(srcs))  # dedup, preserve order
        graph.add_edge(uniq_srcs if len(uniq_srcs) > 1 else uniq_srcs[0], target)

    return sources


def compile_workflow(spec: WorkflowSpec, checkpointer=None, services=None):
    graph = StateGraph(WorkflowState)
    services = services or {}
    bus: RunEventBus | None = services.get("event_bus")

    # 1. Instantiate nodes — config schemas validated here at compile time.
    instances = {}
    for node_spec in spec.nodes:
        node_class = NodeRegistry.get(node_spec.type)
        inst = node_class(
            node_spec.id, node_spec.effective_config(), services=services
        )
        # Carry routing config so runtime_fn can bind it into the gateway.
        inst._allowed_models = node_spec.allowed_models
        inst._model_routing = (
            node_spec.model_routing.model_dump()
            if node_spec.model_routing is not None
            else None
        )
        instances[node_spec.id] = inst

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

    # Production passes a Redis-backed saver through the service container.
    # MemorySaver remains an explicit offline/test fallback only.
    return graph.compile(checkpointer=checkpointer or MemorySaver())
