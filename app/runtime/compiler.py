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
import traceback
from typing import Any
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

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
    clear_pause_request,
    is_pause_requested,
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
from .templating import prune_absent, resolve
from .events import RunEvent, RunEventBus
from .node_events import sanitize_preview, is_graph_interrupt, interrupt_payload

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
                    entity_tokenizer=services.get("entity_tokenizer"),
                    collection_id=state.get("collection_id", "default"),
                    processing_mode=(
                        getattr(instance, "_data_protection_mode", None)
                        or services.get("entity_protection_mode")
                    ),
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

        # Cooperative pause: a run-history "pause" action just sets a flag
        # (app/workflow/run_history.py:request_pause) — nothing can interrupt
        # a node that is already mid-execution (e.g. an in-flight LLM call),
        # so this is the next node boundary reached, checked once up front.
        # If set, park here via the same interrupt()/resume machinery HITL
        # gates use, but tagged "user_requested" so resume doesn't require an
        # approve/reject/edit decision (see app/runtime/hitl.py).
        user_pause_triggered = False
        try:
            with metrics.track_node(type_name):
                resolved = prune_absent(
                    resolve(instance.config.model_dump(), state),
                    instance.config_schema,
                )
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
                # A restart-safe resume with no persistent LangGraph
                # checkpointer configured rebuilds the run from scratch and
                # replays it (see resume_workflow_durable's Mongo-fallback
                # path) — that is a brand-new graph invocation, not
                # Command(resume=...), so LangGraph's own interrupt/resume
                # matching does not apply. That path already injects the
                # paused node's resume decision into
                # services["hitl_resume_decisions"] (the same map
                # HumanInLoopAgent checks) precisely to skip past it; a
                # cooperative pause must honor that marker exactly the same
                # way, or it would re-pause instead of continuing.
                durable_resume_decisions = (
                    services.get("hitl_resume_decisions") or {}
                )
                if node_id not in durable_resume_decisions and (
                    audit is not None and run_id and await is_pause_requested(
                        audit, run_id=run_id, session_id=session_id,
                    )
                ):
                    user_pause_triggered = True
                    # First pass: raises (pause). On an in-process or
                    # persistent-checkpointer resume, the checkpointed call
                    # returns instead of raising — the flag must not be
                    # cleared beforehand, or this call would happen zero
                    # times on replay instead of matching the paused call,
                    # which LangGraph's resume matching requires. Clearing it
                    # here (only reached once actually resumed) also stops it
                    # from re-triggering at the next node.
                    interrupt({"kind": "user_requested_pause", "node_id": node_id})
                    await clear_pause_request(
                        audit, run_id=run_id, session_id=session_id,
                    )
                output = await instance.run(state, resolved)
                extra_state = output.pop("__state__", {}) if isinstance(output, dict) else {}
                validated = instance.output_schema(**output)
                # Materialise declared-but-omitted output fields.
                #
                # Preflight authorises a template reference if the field name
                # exists on the node's output_schema, but templates resolve at
                # runtime against the raw dict run() returned. A field that is
                # declared WITH A DEFAULT and skipped on some code path (e.g.
                # RAGAgent's early "no sources matched" return omits
                # grounding_for_drafter) therefore passed preflight and then
                # died mid-run with "Template path not resolvable" — and only
                # on the branch where retrieval came back empty, so it never
                # showed up in a normal test. Validation already computes those
                # defaults; previously the validated model was discarded and
                # only the raw dict was stored. Merging raw-over-validated is
                # strictly additive: every key run() returned is preserved
                # unchanged (including keys not on the schema), and any
                # declared field run() omitted appears with its declared
                # default instead of being absent.
                if isinstance(output, dict):
                    output = {
                        **validated.model_dump(mode="python"),
                        **output,
                    }
        except BaseException as e:
            # Log to stdout FIRST, before any client emission. A failure that is
            # only published to the SSE bus vanishes the moment the client
            # disconnects (the run then returns 200 with no server-side trace).
            # Server-side logging must not depend on a client being connected.
            # Interrupts are control flow (HITL pause), not errors — info only.
            if is_graph_interrupt(e):
                pause_kind = "user_requested" if user_pause_triggered else "hitl_gate"
                log.info("node_paused", node_id=node_id, type_name=type_name,
                         run_id=run_id, pause_kind=pause_kind)
                if audit is not None and run_id:
                    await record_node_paused(
                        audit,
                        run_id=run_id,
                        session_id=session_id,
                        node_id=node_id,
                        paused_at=time.time(),
                        pause_kind=pause_kind,
                    )
                    await record_checkpoint_paused(
                        audit,
                        run_id=run_id,
                        session_id=session_id,
                        node_id=node_id,
                        pause_context={
                            "interrupt": interrupt_payload(e),
                        },
                        pause_kind=pause_kind,
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
                    # Trimmed from the end, not the start — the frames nearest
                    # the raise are the useful ones for a non-technical user's
                    # "suggested corrective action" and for debugging, whereas
                    # the outermost frames are just this same wrapper.
                    formatted_traceback = "".join(
                        traceback.format_exception(type(e), e, e.__traceback__)
                    )[-4000:]
                    await record_node_failed(
                        audit,
                        run_id=run_id,
                        session_id=session_id,
                        node_id=node_id,
                        type_name=type_name,
                        error=str(e)[:500],
                        error_type=type(e).__name__,
                        error_traceback=formatted_traceback,
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

    Mixed fan-in (a target reached by more than one "arrival group" — a
    HITL router's dispatch, a plain-edge router's (condition+branches)
    branch, or the combined plain-edge group) needs special handling — see
    the join-gate logic below. Verified empirically (not just read from
    docs) against langgraph 1.2.9: a target with two SEPARATE incoming
    registrations races (fires as soon as ANY one of them arrives), not an
    AND-join, EXCEPT when the predecessors are true siblings scheduled in
    the same tick. The moment ANY branch feeding a shared target passes
    through a genuine interrupt() (a HumanInLoopAgent pause + later
    out-of-band resume) — even indirectly, through an ordinary router node
    downstream of the paused branch — a faster sibling branch reaches the
    shared target and fires it BEFORE the paused branch ever resumes; its
    template references to the not-yet-run branch's node then fail
    (KeyError: Template path not resolvable / a Pydantic error on whatever
    got substituted). Confirmed for both the direct HITL case and the
    HITL-behind-a-router case with a minimal raw-LangGraph reproduction
    before writing this fix. LangGraph only genuinely waits for every
    predecessor when they're passed together as a list in ONE
    add_edge([...], target) call — true for plain-only fan-in already (see
    step 6), and now made true for HITL- and router-involving fan-in too,
    via a synthetic pass-through "join gate" node that a conditional
    dispatch (HITL or router) targets instead of the shared node directly,
    combined into that one list.
    """
    sources: set[str] = set()

    # 1. Collect all targets for each HITL node (across its multiple edges).
    hitl_targets: dict[str, list[str]] = {}
    for edge in edges:
        if edge.from_ in hitl_ids and not (edge.condition and edge.branches):
            tgts = edge.to if isinstance(edge.to, list) else [edge.to]
            hitl_targets.setdefault(edge.from_, []).extend(tgts)

    # 2. Collect router (condition+branches) edges separately — each one is
    # its own conditional-dispatch arrival group, distinct per source node.
    router_edges = [e for e in edges if e.condition and e.branches]

    # 3. Collect plain edges. Must happen before step 4, which needs to know
    # which conditional targets also have other (non-conditional)
    # predecessors before deciding whether they need a join gate.
    plain_targets: dict[str, list[str]] = {}
    for edge in edges:
        sources.add(edge.from_)
        if edge.from_ in hitl_ids and not (edge.condition and edge.branches):
            continue  # HITL nodes wired separately (step 5)
        if edge.condition and edge.branches:
            continue  # routers wired separately (step 6)
        targets = edge.to if isinstance(edge.to, list) else ([edge.to] if edge.to else [])
        for target in targets:
            plain_targets.setdefault(target, []).append(edge.from_)

    # A target reached by exactly one "arrival group" (one HITL router's
    # dispatch, XOR one router branch, XOR the one combined plain-edge
    # group) is unaffected by the race above and is wired directly, exactly
    # as before. A target reached by MORE than one group is where the race
    # lives — every conditional group feeding it gets redirected through a
    # join gate (steps 5-6) so all groups can be combined into one
    # add_edge([...], target) call (step 7).
    target_group_counts: dict[str, int] = {}
    for hitl_id, targets in hitl_targets.items():
        for target in dict.fromkeys(targets):
            target_group_counts[target] = target_group_counts.get(target, 0) + 1
    for edge in router_edges:
        for target in dict.fromkeys(edge.branches.values()):
            target_group_counts[target] = target_group_counts.get(target, 0) + 1
    for target in plain_targets:
        target_group_counts[target] = target_group_counts.get(target, 0) + 1

    join_gates_by_target: dict[str, list[str]] = {}

    def _register_join_gate(gate_id: str, target: str) -> None:
        if gate_id in join_gates_by_target.get(target, []):
            return  # already registered (e.g. two branches of one router share a target)

        async def _join_gate(state: dict) -> dict:
            return {}

        graph.add_node(gate_id, _join_gate)
        join_gates_by_target.setdefault(target, []).append(gate_id)

    # 4. Register exactly one conditional router per HITL node.
    for hitl_id, targets in hitl_targets.items():
        uniq = list(dict.fromkeys(targets))  # dedup, preserve order
        dispatch_targets: list[str] = []
        for target in uniq:
            if target_group_counts.get(target, 1) > 1:
                gate_id = f"__hitl_join__{hitl_id}__{target}"
                _register_join_gate(gate_id, target)
                dispatch_targets.append(gate_id)
            else:
                dispatch_targets.append(target)

        def _hitl_router(state: dict, _targets=dispatch_targets, _hid=hitl_id):
            decision = (
                state.get("node_outputs", {})
                .get(_hid, {})
                .get("decision")
            )
            if decision == "reject":
                return END
            # approve / edit -> proceed to ALL fan-out targets in parallel
            return _targets

        graph.add_conditional_edges(hitl_id, _hitl_router, [*dispatch_targets, END])

    # 5. Wire routers (condition+branches), redirecting any branch whose
    # target has other arrival groups through a join gate the same way.
    for edge in router_edges:
        branch_map: dict[str, str] = {}
        for decision, target in edge.branches.items():
            if target_group_counts.get(target, 1) > 1:
                gate_id = f"__router_join__{edge.from_}__{target}"
                _register_join_gate(gate_id, target)
                branch_map[decision] = gate_id
            else:
                branch_map[decision] = target

        def _router(state: dict, _edge=edge, _branch_map=branch_map) -> str:
            decision = state["node_outputs"][_edge.from_].get("route")
            if decision not in _edge.branches:
                raise ValueError(
                    f"Router {_edge.from_} returned unknown route {decision!r}; "
                    f"expected one of {list(_edge.branches)}"
                )
            return _branch_map[decision]

        graph.add_conditional_edges(
            edge.from_, _router, list(dict.fromkeys(branch_map.values()))
        )

    # 6. Plain edges (and any join gates from steps 4-5) into the SAME
    # target are collected and wired with one add_edge(sources, target)
    # call instead of one add_edge() per source — this is what makes
    # LangGraph treat multiple predecessors as an AND-join (wait for ALL of
    # them).
    all_fan_in_targets = set(plain_targets) | set(join_gates_by_target)
    for target in all_fan_in_targets:
        combined = list(dict.fromkeys(plain_targets.get(target, [])))
        for gate_id in join_gates_by_target.get(target, []):
            if gate_id not in combined:
                combined.append(gate_id)
        graph.add_edge(combined if len(combined) > 1 else combined[0], target)

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
        inst._data_protection_mode = node_spec.data_protection_mode
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
