"""Builder-support API — everything the visual authoring experience needs.

These endpoints exist so the Builder can be *specific* instead of generic: it can
show what a step will actually produce, run one step against a pasted example,
simulate a whole workflow and explain each decision, and offer only the operators
a field's type permits.

    /operators          typed operator catalog          (rule editor)
    /output-contract    typed field tree per node       (mapping picker, rules)
    /node-test          run one node on sample data     (Test tab)
    /simulate           run a workflow in memory        (Simulator)
    /assist/schema      AI proposes an output schema    (Ask AI)
    /assist/rules       AI proposes business rules      (Ask AI)
    /email/connections  configured mailboxes            (Email node)

Two rules hold throughout:

*   **Nothing here mutates a saved workflow.** Every endpoint takes the YAML the
    Builder currently has in memory and returns a result. Testing a node cannot
    change what is stored.
*   **The AI assists return editable configuration, never applied changes.** A
    suggested schema or rule set comes back as data the author reviews in the
    normal editor. Once accepted it is ordinary deterministic configuration —
    nothing keeps consulting a model at run time.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.llm.model_catalog import AUTO_MODEL
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger
from app.runtime.field_schema import (
    FieldSpec,
    describe_schema,
    field_paths,
    json_schema,
    parse_fields,
)
from app.runtime.loader import load_workflow_from_string
from app.runtime.logic_preflight import build_field_index
from app.runtime.preflight import preflight_workflow_yaml
from app.runtime.rules import (
    OPERATOR_LABELS,
    OPERATORS_BY_TYPE,
    SET_OPERATORS,
    UNARY_OPERATORS,
    Rule,
)
from app.runtime.schema import WorkflowSpec
from app.runtime.templating import resolve
from app.security.dependencies import CurrentUser, require_consultant

log = get_logger(__name__)

router = APIRouter(prefix="/api/builder", tags=["builder"])

#: Models used by the authoring assistants. Deliberately fixed rather than
#: author-selectable: these calls shape configuration the author then reviews,
#: so predictability matters more than letting someone pick a cheaper model for
#: a suggestion they will read once.
SCHEMA_ASSIST_MODEL = "gpt-5.6-luna"
RULE_ASSIST_MODEL = "gpt-5.6-luna"

#: A simulation runs real nodes, including real model calls. This caps how long
#: the request may take, so a runaway workflow cannot hold a connection open —
#: the Cockpit is the place for long runs.
SIMULATION_TIMEOUT_SECONDS = 180


def _services(request: Request) -> dict[str, Any]:
    return getattr(request.app.state, "services", {}) or {}


# --------------------------------------------------------------------------
# Operator catalog
# --------------------------------------------------------------------------

@router.get("/operators")
def operators(user: CurrentUser = Depends(require_consultant)) -> dict[str, Any]:
    """Which operators apply to which field type, and how to render each one.

    The rule editor drives its dropdowns from this rather than from a hardcoded
    list, so the operators a user can pick are exactly the ones the runtime
    implements and preflight accepts. `arity` tells the editor whether to show a
    value input at all, one value, or a list of alternatives.
    """
    del user
    return {
        "by_type": {
            field_type: list(items)
            for field_type, items in OPERATORS_BY_TYPE.items()
        },
        "labels": dict(OPERATOR_LABELS),
        "arity": {
            operator: (
                "none"
                if operator in UNARY_OPERATORS
                else "many"
                if operator in SET_OPERATORS
                else "one"
            )
            for operator in OPERATOR_LABELS
        },
    }


# --------------------------------------------------------------------------
# Output contract
# --------------------------------------------------------------------------

class ContractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_yaml: str
    #: When set, only values that can reach this node are returned — a mapping
    #: picker must not offer a value from a step that runs later.
    node_id: str | None = None


class ContractField(BaseModel):
    path: str
    reference: str
    type: str
    description: str = ""
    required: bool = True
    may_be_unavailable: bool = False
    enum_values: list[str] = Field(default_factory=list)
    item_type: str | None = None
    operators: list[str] = Field(default_factory=list)


class ContractNode(BaseModel):
    node_id: str
    type_name: str
    label: str
    execution_kind: str
    typed: bool
    fields: list[ContractField] = Field(default_factory=list)


@router.post("/output-contract")
def output_contract(
    body: ContractRequest,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """What each step guarantees, as typed dotted paths (§40).

    Computed from the same index preflight uses, so a path this returns is a
    path preflight will authorise, and the operators listed here are the ones a
    rule against that path may use. That equivalence is the whole point: the
    editor cannot build something the validator then rejects.
    """
    del user
    spec = _parse_workflow(body.workflow_yaml)
    index = build_field_index(spec)

    upstream = (
        _ancestors(spec, body.node_id) if body.node_id else {node.id for node in spec.nodes}
    )

    nodes: list[ContractNode] = []
    for node in spec.nodes:
        if node.id not in upstream or node.id == body.node_id:
            continue
        paths = index.paths_for(node.id)
        nodes.append(
            ContractNode(
                node_id=node.id,
                type_name=node.type,
                label=_label_of(node),
                execution_kind=_execution_kind(node.type),
                typed=index.typed_nodes.get(node.id, False),
                fields=[
                    ContractField(
                        path=item.path,
                        # The reference form is what a mapping writes into config,
                        # so the author never has to construct it by hand.
                        reference=f"{{{{outputs.{node.id}.{item.path}}}}}",
                        type=item.type,
                        description=item.description,
                        required=item.required,
                        may_be_unavailable=item.may_be_unavailable,
                        enum_values=item.enum_values,
                        item_type=item.item_type,
                        operators=list(
                            OPERATORS_BY_TYPE.get(
                                item.type, OPERATORS_BY_TYPE["unknown"]
                            )
                        ),
                    )
                    for item in sorted(paths, key=_contract_sort_key)
                ],
            )
        )

    return {
        "nodes": [node.model_dump() for node in nodes],
        "inputs": [
            {
                "name": name,
                "reference": f"{{{{inputs.{name}}}}}",
                "type": input_spec.type,
                "description": input_spec.description or "",
                "required": input_spec.required,
            }
            for name, input_spec in spec.inputs.items()
        ],
        "variables": [
            {
                "name": variable.name,
                "reference": f"{{{{variables.{variable.name}}}}}",
                "type": variable.type,
            }
            for variable in spec.static_variables
        ],
    }


# --------------------------------------------------------------------------
# Schema compilation preview
# --------------------------------------------------------------------------

class SchemaPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_fields: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/schema-preview")
def schema_preview(
    body: SchemaPreviewRequest,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Compile visual schema rows and show what the model will be held to.

    Lets the schema builder surface a validation error on the row that caused it
    while the author is still editing, instead of at run time — and shows the
    generated JSON Schema for anyone who wants to see it.
    """
    del user
    try:
        fields = parse_fields(body.output_fields)
        compiled = json_schema(fields)
    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail={"message": str(error), "field_count": len(body.output_fields)},
        ) from error

    return {
        "json_schema": compiled,
        "contract": describe_schema(fields),
        "paths": [item.model_dump() for item in field_paths(fields)],
    }


# --------------------------------------------------------------------------
# Single-node test
# --------------------------------------------------------------------------

class NodeTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_name: str
    config: dict[str, Any] = Field(default_factory=dict)
    node_id: str = "test_node"
    #: Sample workflow inputs, e.g. {"message": "Unsere Pumpe ist ausgefallen"}.
    inputs: dict[str, Any] = Field(default_factory=dict)
    #: Sample upstream outputs keyed by node id, so a node that reads
    #: {{outputs.extract.result.intent}} can be tested without running extract.
    upstream_outputs: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


@router.post("/node-test")
async def node_test(
    body: NodeTestRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Run one node against sample data (§21).

    This is a real execution: the node's own config validation runs, templates
    resolve against the sample state, and an AI Task really calls the model. What
    it is *not* is a workflow run — no run record, no history, no saved changes —
    because the point is a tight edit/test loop while configuring one step.
    """
    services = _services(request)
    try:
        node_class = NodeRegistry.get(body.type_name)
    except Exception as error:
        raise HTTPException(
            status_code=404, detail=f"Unknown node type {body.type_name!r}"
        ) from error

    # External side effects are refused here on purpose. A test is something an
    # author runs repeatedly while tweaking wording; a send is not.
    if _is_external_write(body.type_name, body.config):
        raise HTTPException(
            status_code=400,
            detail=(
                "This operation acts outside the platform and cannot be run "
                "from the Test tab. Switch the operation to Search, Read or "
                "Create Draft to test the configuration, and use a workflow run "
                "when you intend the real action."
            ),
        )

    session_id = getattr(user, "session_id", None) or user.username
    state = {
        "inputs": {
            **body.inputs,
            "SYSTEM.run_id": f"node-test-{uuid.uuid4().hex[:8]}",
            "SYSTEM.session_id": session_id,
        },
        "node_outputs": dict(body.upstream_outputs),
        "variables": dict(body.variables),
        "audit_log": [],
        "session_id": session_id,
        "collection_id": "default",
        "domain_state": {},
    }

    started = time.perf_counter()
    try:
        instance = node_class(body.node_id, body.config, services=services)
    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail={"stage": "config", "message": str(error)},
        ) from error

    # Bind the cost-tracking gateway clone exactly as the compiler does, so a
    # node test's token spend lands in the same ledger as a run's.
    llm = services.get("llm")
    if llm is not None and hasattr(llm, "with_context"):
        instance.services = {
            **services,
            "llm": llm.with_context(
                run_id=state["inputs"]["SYSTEM.run_id"],
                session_id=session_id,
                node_id=body.node_id,
                ledger=services.get("cost_ledger"),
                node_type=body.type_name,
            ),
        }

    try:
        resolved = resolve(instance.config.model_dump(), state)
    except KeyError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "stage": "mapping",
                "message": str(error),
                "hint": (
                    "The configuration references a value the sample data does "
                    "not contain. Add it under sample inputs or upstream "
                    "outputs."
                ),
            },
        ) from error

    try:
        output = await instance.run(state, resolved)
    except Exception as error:
        return {
            "status": "failed",
            "node_id": body.node_id,
            "type_name": body.type_name,
            "error": str(error)[:1000],
            "error_type": type(error).__name__,
            "duration_s": round(time.perf_counter() - started, 3),
            "resolved_config": _previewable(resolved),
        }

    output.pop("__state__", None)
    return {
        "status": "completed",
        "node_id": body.node_id,
        "type_name": body.type_name,
        "output": _previewable(output),
        "duration_s": round(time.perf_counter() - started, 3),
        "resolved_config": _previewable(resolved),
        "explanation": _explain(body.type_name, output),
    }


# --------------------------------------------------------------------------
# Workflow simulation
# --------------------------------------------------------------------------

class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_yaml: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    #: Freeze one or more nodes to a fixed output instead of running them. This
    #: is what makes "set confidence to 0.64 and rerun" a two-second demo rather
    #: than a hunt for an example the model happens to be unsure about.
    stub_outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    #: Stop before this node, so a branch can be simulated in isolation.
    until_node: str | None = None


@router.post("/simulate")
async def simulate(
    body: SimulateRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Run a workflow in memory and return a per-step trace (§23, §24, §47).

    Reuses the ordinary runtime — the same compiler, the same nodes, the same
    preflight — so a simulation that works is evidence about the real workflow,
    not about a separate simulation engine. What it skips is the durable run
    record: a simulation is for understanding a workflow, not for auditing one.

    `stub_outputs` uses the runtime's existing reused-node mechanism, the same
    one a retry uses to replay completed nodes, so a stubbed node costs no
    tokens and the rest of the graph is genuinely executed against it.
    """
    from app.runtime.executor import run_workflow

    services = _services(request)
    spec = _parse_workflow(body.workflow_yaml)

    report = preflight_workflow_yaml(
        body.workflow_yaml, provided_inputs=body.inputs, compile_graph=False
    )
    if not report.valid:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    f"Simulation blocked by {len(report.errors)} preflight "
                    "error(s). Nothing was run."
                ),
                "preflight": report.model_dump(mode="json"),
            },
        )

    if body.until_node:
        spec = _slice_through(spec, body.until_node)

    unknown_stubs = sorted(
        set(body.stub_outputs) - {node.id for node in spec.nodes}
    )
    if unknown_stubs:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot stub unknown step(s): {unknown_stubs}",
        )

    session_id = getattr(user, "session_id", None) or user.username
    simulation_id = f"sim-{uuid.uuid4().hex[:10]}"

    # Deliberately no audit_db / event_bus: a simulation must not create a run
    # record or publish run events that the Cockpit would show as a real run.
    simulation_services = {
        key: value
        for key, value in services.items()
        if key not in ("audit_db", "event_bus")
    }

    started = time.perf_counter()
    try:
        result = await run_workflow(
            spec,
            body.inputs,
            session_id,
            collection_id="default",
            services=simulation_services,
            run_id=simulation_id,
            reused_node_results={
                node_id: {"output": output, "extra_state": {}}
                for node_id, output in body.stub_outputs.items()
            },
            retry_source_run_id="simulation" if body.stub_outputs else None,
        )
    except Exception as error:
        log.warning(
            "builder.simulation_failed",
            simulation_id=simulation_id,
            error=str(error),
        )
        return {
            "simulation_id": simulation_id,
            "status": "failed",
            "error": str(error)[:1000],
            "error_type": type(error).__name__,
            "duration_s": round(time.perf_counter() - started, 3),
            "steps": [],
        }

    state = result.get("state") or {}
    steps = _timeline(spec, state, stubbed=set(body.stub_outputs))
    # A workflow that reaches a Human Review gate *pauses* there — the gate never
    # completes, so it has no output and would be missing from the trace. But
    # "the request was escalated to a person" is the outcome the author is
    # demonstrating, so the waiting gate is appended as a step of its own. The
    # canvas then highlights it as awaiting a decision rather than simply
    # stopping one node early, which would read as a failure.
    waiting = _waiting_steps(spec, result.get("interrupt"))
    steps.extend(waiting)

    return {
        "simulation_id": simulation_id,
        "status": result.get("status"),
        "duration_s": round(time.perf_counter() - started, 3),
        "steps": steps,
        "path": [*_executed_path(state), *(step["node_id"] for step in waiting)],
        "output": _previewable(result.get("output")),
        "interrupt": _previewable(_interrupt_values(result.get("interrupt"))),
        "waiting_for": [step["node_id"] for step in waiting],
        "stubbed": sorted(body.stub_outputs),
    }


def _interrupt_values(interrupt: Any) -> list[dict[str, Any]]:
    """Pull the real payloads out of LangGraph's Interrupt objects.

    ``__interrupt__`` is a sequence of ``Interrupt`` instances, each carrying the
    dict the node passed to ``interrupt(...)`` on ``.value``. Serialising the
    sequence itself would produce an opaque object; the payload is what holds the
    review question, the panels and the allowed actions.
    """
    if not interrupt:
        return []
    items = interrupt if isinstance(interrupt, (list, tuple)) else [interrupt]
    values: list[dict[str, Any]] = []
    for item in items:
        value = getattr(item, "value", item)
        if isinstance(value, dict):
            values.append(value)
    return values


def _waiting_steps(
    spec: WorkflowSpec, interrupt: Any
) -> list[dict[str, Any]]:
    """One step per gate the run is parked at."""
    labels = {node.id: _label_of(node) for node in spec.nodes}
    types = {node.id: node.type for node in spec.nodes}

    steps: list[dict[str, Any]] = []
    for payload in _interrupt_values(interrupt):
        node_id = payload.get("node_id")
        if not node_id:
            continue
        type_name = types.get(node_id, "HumanInLoopAgent")
        steps.append(
            {
                "node_id": node_id,
                "label": labels.get(node_id, node_id),
                "type_name": type_name,
                "execution_kind": _execution_kind(type_name),
                "stubbed": False,
                "status": "waiting",
                "duration_s": None,
                "output": None,
                "review": _previewable(payload),
                "explanation": {
                    "kind": _execution_kind(type_name),
                    "decided_by": "Awaiting a human decision",
                    "summary": [
                        payload.get("question")
                        or "This step is waiting for a person to decide."
                    ],
                },
            }
        )
    return steps


def _timeline(
    spec: WorkflowSpec, state: dict[str, Any], stubbed: set[str]
) -> list[dict[str, Any]]:
    """Per-step trace in execution order, with each step's own explanation.

    Ordered by the audit log rather than by declaration order, so a branch that
    did not run is visibly absent instead of appearing with an empty output —
    which is what makes the canvas animation honest.
    """
    node_types = {node.id: node.type for node in spec.nodes}
    labels = {node.id: _label_of(node) for node in spec.nodes}
    outputs = state.get("node_outputs") or {}
    durations = {
        entry.get("node_id"): entry.get("duration_s")
        for entry in (state.get("audit_log") or [])
        if isinstance(entry, dict)
    }

    ordered: list[str] = []
    for entry in state.get("audit_log") or []:
        node_id = entry.get("node_id") if isinstance(entry, dict) else None
        if node_id and node_id in outputs and node_id not in ordered:
            ordered.append(node_id)
    for node_id in outputs:
        if node_id not in ordered:
            ordered.append(node_id)

    steps: list[dict[str, Any]] = []
    for node_id in ordered:
        type_name = node_types.get(node_id, "unknown")
        output = outputs.get(node_id) or {}
        steps.append(
            {
                "node_id": node_id,
                "label": labels.get(node_id, node_id),
                "type_name": type_name,
                "execution_kind": _execution_kind(type_name),
                "stubbed": node_id in stubbed,
                "duration_s": durations.get(node_id),
                "output": _previewable(output),
                "explanation": _explain(type_name, output),
            }
        )
    return steps


def _executed_path(state: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for entry in state.get("audit_log") or []:
        node_id = entry.get("node_id") if isinstance(entry, dict) else None
        if node_id and node_id not in seen:
            seen.append(node_id)
    return seen


def _explain(type_name: str, output: dict[str, Any]) -> dict[str, Any]:
    """Turn a node's output into the "why did this happen?" view (§24, §47).

    Every step reports which kind of thing decided its outcome — a model, a
    rule, an external system, a person — because that distinction is what a
    reviewer actually needs and what a chat transcript cannot show.
    """
    if not isinstance(output, dict):
        return {"kind": _execution_kind(type_name), "summary": []}

    kind = _execution_kind(type_name)

    if type_name == "AITaskAgent":
        result = output.get("result") or {}
        summary = [
            f"{key} = {_short(value)}"
            for key, value in list(result.items())[:12]
            if key not in ("reasoning",)
        ]
        return {
            "kind": kind,
            "decided_by": "AI inference",
            "status": output.get("status"),
            "confidence": output.get("confidence"),
            "detected_language": output.get("detected_language"),
            "model_used": output.get("model_used"),
            "reasoning": output.get("reasoning"),
            "summary": summary,
        }

    if type_name == "DecisionAgent":
        return {
            "kind": kind,
            "decided_by": "Deterministic rules",
            "matched_rules": output.get("matched_rules") or [],
            "decisions": _previewable(output.get("decisions") or {}),
            "summary": output.get("summary") or [],
            "rules": _previewable(output.get("explanation") or []),
        }

    if type_name == "RouterAgent":
        return {
            "kind": kind,
            "decided_by": "Deterministic routing",
            "route": output.get("route"),
            "route_value": output.get("route_value"),
            "used_fallback": bool(output.get("used_fallback")),
            "summary": output.get("matched_conditions") or (
                [output["reason"]] if output.get("reason") else []
            ),
            "conditions": _previewable(output.get("explanation") or []),
        }

    if type_name == "HumanInLoopAgent":
        return {
            "kind": kind,
            "decided_by": "Human decision",
            "decision": output.get("decision"),
            "summary": [
                f"decision = {output.get('decision')}"
                + (f" ({output['reason']})" if output.get("reason") else "")
            ],
        }

    if type_name == "EmailAgent":
        return {
            "kind": kind,
            "decided_by": "External system",
            "operation": output.get("operation"),
            "deduplicated": bool(output.get("deduplicated")),
            "summary": [
                f"{output.get('operation')} via {output.get('provider')}",
                *(
                    [f"{output.get('message_count')} message(s)"]
                    if output.get("message_count")
                    else []
                ),
            ],
        }

    if type_name == "DataTransformAgent":
        data = output.get("data") or {}
        return {
            "kind": kind,
            "decided_by": "Deterministic transform",
            "summary": [
                f"{key} = {_short(value)}" for key, value in list(data.items())[:12]
            ],
            "defaulted": output.get("defaulted") or [],
        }

    if type_name == "WorkflowInputAgent":
        data = output.get("data") or {}
        missing = output.get("missing") or []
        return {
            "kind": kind,
            "decided_by": f"Received as {output.get('source', 'input')}",
            "summary": [
                *(f"{key} = {_short(value)}" for key, value in list(data.items())[:12]),
                *(
                    [f"missing required: {', '.join(missing)}"]
                    if missing
                    else []
                ),
            ],
        }

    return {
        "kind": kind,
        "decided_by": (
            "AI inference" if kind == "ai" else "Deterministic step"
        ),
        "summary": [
            f"{key} = {_short(value)}" for key, value in list(output.items())[:8]
        ],
    }


# --------------------------------------------------------------------------
# AI authoring assistants
# --------------------------------------------------------------------------

class SchemaAssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: What the author wants extracted, in their own words.
    description: str
    #: An optional real example of the content this schema will be applied to.
    sample_content: str = ""
    existing_fields: list[dict[str, Any]] = Field(default_factory=list)


class ProposedField(BaseModel):
    """One suggested row. Mirrors FieldSpec, so the response drops straight into
    the schema builder as editable rows rather than as prose the author has to
    transcribe."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    nullable: bool = False
    enum_values: list[str] = Field(default_factory=list)
    item_type: str | None = None
    item_enum_values: list[str] = Field(default_factory=list)
    fields: list["ProposedField"] = Field(default_factory=list)


ProposedField.model_rebuild()


class ProposedSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[ProposedField]
    notes: str = ""


@router.post("/assist/schema")
async def assist_schema(
    body: SchemaAssistRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Propose a structured-output schema from a description (§37).

    The proposal comes back as editable rows and is validated through the same
    compiler the runtime uses before it is returned — so a suggestion the author
    accepts is known to compile, and a suggestion that doesn't is reported as a
    failed suggestion rather than pasted into the editor to fail later.
    """
    del user
    llm = _require_llm(request)

    system = (
        "You design structured-output schemas for business workflows. You are "
        "given what a person wants extracted from unstructured content, and you "
        "propose the fields.\n\n"
        "Rules:\n"
        "- Use snake_case names that a business reader recognises.\n"
        "- Prefer an enum over free text whenever the set of answers is closed. "
        "Always include a catch-all value such as 'other' in an enum.\n"
        "- Make a field nullable when the source content may simply not state "
        "it. That is how the model is told to return null instead of guessing.\n"
        "- Group related values into an object (customer, equipment, process) "
        "rather than flattening everything.\n"
        "- Use a list of strings for 'anything still missing' style fields.\n"
        "- Every field needs a description written as an instruction to whoever "
        "fills it in.\n"
        "- Types available: string, text, number, integer, boolean, enum, "
        "object, list, date. A list must declare item_type. An object must "
        "declare its child fields.\n"
        "- Do not add a confidence field; the platform adds one."
    )
    user_prompt = f"# What to extract\n{body.description.strip()}"
    if body.sample_content.strip():
        user_prompt += (
            "\n\n# Example of the content this will be applied to\n"
            f"{body.sample_content.strip()[:6000]}"
        )
    if body.existing_fields:
        user_prompt += (
            "\n\n# Fields already defined (keep them unless they are wrong)\n"
            f"{[field.get('name') for field in body.existing_fields]}"
        )

    try:
        proposal = await llm.complete_structured(
            model=SCHEMA_ASSIST_MODEL,
            system=system,
            user=user_prompt,
            response_model=ProposedSchema,
            temperature=0.1,
            max_tokens=4096,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Schema suggestion failed: {error}"
        ) from error

    rows = [field.model_dump(exclude_none=True) for field in proposal.fields]
    try:
        compiled = parse_fields(rows)
    except Exception as error:
        # Report rather than repair: silently "fixing" a suggestion would hide
        # that the model produced something invalid, and the author would have
        # no idea which row to distrust.
        return {
            "status": "invalid",
            "message": f"The suggested schema does not compile: {error}",
            "fields": rows,
            "notes": proposal.notes,
        }

    return {
        "status": "ok",
        "fields": [item.model_dump(mode="json") for item in compiled],
        "contract": describe_schema(compiled),
        "notes": proposal.notes,
    }


class RuleAssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The business rule in the author's own words.
    description: str
    #: Available field paths with their types, from /output-contract. Passing
    #: them is what keeps a suggestion addressable: without them the model
    #: invents plausible field names that do not exist in this workflow.
    available_fields: list[dict[str, Any]] = Field(default_factory=list)


class ProposedRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[dict[str, Any]]
    notes: str = ""


@router.post("/assist/rules")
async def assist_rules(
    body: RuleAssistRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Turn a described business rule into deterministic configuration (§36).

    The result is a normal rule the author reviews and accepts. Nothing about
    the running workflow consults a model afterwards — the model wrote the
    configuration once; the rule engine evaluates it every time.
    """
    del user
    llm = _require_llm(request)

    fields_block = "\n".join(
        f"- {item.get('reference') or item.get('path')} "
        f"({item.get('type', 'unknown')})"
        + (
            f" allowed values: {item.get('enum_values')}"
            if item.get("enum_values")
            else ""
        )
        for item in body.available_fields[:150]
    ) or "(none supplied — do not invent field paths)"

    system = (
        "You convert business rules described in plain language into structured "
        "IF/THEN rules for a deterministic rule engine. Return JSON only.\n\n"
        "Rule shape:\n"
        '{"name": str, "description": str,\n'
        ' "when": {"operator": "and"|"or"|"not", "conditions": [ ... ]},\n'
        ' "then": [{"field": str, "operation": "set"|"append"|"increase"|'
        '"decrease", "value": any}]}\n\n'
        "A condition is {\"field\": <path>, \"operator\": <operator>, "
        '"value": <value>}. A condition group may nest inside conditions.\n\n'
        f"Operators by field type: {OPERATORS_BY_TYPE}\n"
        f"Operators needing no value: {sorted(UNARY_OPERATORS)}\n"
        f"Operators needing a list of alternatives: {sorted(SET_OPERATORS)}\n\n"
        "Hard rules:\n"
        "- Use ONLY field paths from the available-fields list, written without "
        "the {{ }} braces.\n"
        "- Use only operators valid for that field's type.\n"
        "- For an enum field, use only its allowed values.\n"
        "- `then` field names are new business facts you are establishing "
        "(human_review, urgency, clarification_required) — plain snake_case "
        "names, not paths.\n"
        "- If the description cannot be expressed against the available fields, "
        "return an empty rules list and explain why in notes."
    )
    user_prompt = (
        f"# Business rule to express\n{body.description.strip()}\n\n"
        f"# Available fields\n{fields_block}"
    )

    try:
        proposal = await llm.complete_structured(
            model=RULE_ASSIST_MODEL,
            system=system,
            user=user_prompt,
            response_model=ProposedRules,
            temperature=0.0,
            max_tokens=4096,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Rule suggestion failed: {error}"
        ) from error

    validated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in proposal.rules:
        try:
            # Validating through the runtime's own Rule model is what makes the
            # suggestion trustworthy: anything the engine could not evaluate is
            # reported as rejected instead of landing in the editor.
            validated.append(Rule.model_validate(raw).model_dump(mode="json"))
        except Exception as error:
            rejected.append({"rule": raw, "error": str(error)[:300]})

    return {
        "status": "ok" if validated else "empty",
        "rules": validated,
        "rejected": rejected,
        "notes": proposal.notes,
    }


# --------------------------------------------------------------------------
# Email connections
# --------------------------------------------------------------------------

@router.get("/mcp/servers")
def mcp_servers(
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Configured MCP servers for the Builder's connection panel.

    Names, health, write policy, and which credentials are expected — never a
    credential value. A workflow references a server by id, so this is the whole
    surface the Builder needs.
    """
    del user
    service = _services(request).get("mcp")
    if service is None:
        return {"servers": [], "configured": False}
    return {"servers": service.describe_servers(), "configured": True}


@router.get("/mcp/servers/{server_id}/tools")
async def mcp_tools(
    server_id: str,
    request: Request,
    refresh: bool = False,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Discover what a server can do (§5).

    Asked of the server, never hardcoded: a tool added to the MCP server appears
    in the Builder with no frontend change. Each entry carries its input schema
    (which the Builder renders as a form), its output schema (which drives the
    mapping picker), and its operation class.
    """
    del user
    service = _require_mcp(request)
    try:
        tools = await service.discover_tools(server_id, refresh=refresh)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        # A server that is down must not 500 the Builder — the author needs to
        # see *which* connection is unreachable, and keep editing meanwhile.
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(error),
                "server_id": server_id,
                "code": getattr(error, "code", "MCP_SERVER_UNAVAILABLE"),
            },
        ) from error
    return {"server_id": server_id, "tools": tools, "count": len(tools)}


@router.get("/mcp/servers/{server_id}/health")
async def mcp_health(
    server_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Live connection check, for the panel's Test Connection button."""
    del user
    service = _require_mcp(request)
    if server_id not in service.registry:
        raise HTTPException(
            status_code=404, detail=f"MCP server {server_id!r} is not configured"
        )
    return await service.health_check(server_id)


class MCPToolTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/mcp/test-tool")
async def mcp_test_tool(
    body: MCPToolTestRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Run one MCP tool with literal arguments (§21).

    Invaluable when building live: type a company name, press Test, see the CRM
    answer. Write tools are refused here for the same reason an email send is —
    a test is something an author runs repeatedly while adjusting inputs, and a
    CRM record created twenty times during a demo is a real mess in a real
    system.
    """
    service = _require_mcp(request)
    descriptor = None
    try:
        descriptor = await service.find_tool(body.server_id, body.tool)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail={"message": str(error), "server_id": body.server_id},
        ) from error

    if descriptor is None:
        raise HTTPException(
            status_code=404,
            detail=f"{body.server_id} does not expose a tool called {body.tool!r}",
        )

    if descriptor["operation"] != "read":
        raise HTTPException(
            status_code=400,
            detail=(
                f"{body.tool} is classified as a {descriptor['operation']} "
                "operation and cannot be run from the Test panel. Test it in a "
                "workflow run, where the approval and idempotency rules apply."
            ),
        )

    started = time.perf_counter()
    try:
        result = await service.call(
            server_id=body.server_id,
            tool_name=body.tool,
            arguments=body.arguments,
            run_id=f"tool-test-{uuid.uuid4().hex[:8]}",
            node_id="tool_test",
            session_id=getattr(user, "session_id", None) or user.username,
        )
    except Exception as error:
        payload = getattr(error, "as_payload", None)
        return {
            "status": "failed",
            "server_id": body.server_id,
            "tool": body.tool,
            "duration_s": round(time.perf_counter() - started, 3),
            "error": payload() if callable(payload) else {"message": str(error)},
        }

    return {
        "status": "completed",
        "server_id": body.server_id,
        "tool": body.tool,
        "operation": result["operation"],
        "mode": result["mode"],
        "duration_s": result["duration_s"],
        "is_structured": result["is_structured"],
        "data": _previewable(result["data"]),
        "text": _previewable(result["text"]),
    }


def _require_mcp(request: Request) -> Any:
    service = _services(request).get("mcp")
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="No MCP server is configured in this deployment.",
        )
    return service


@router.get("/email/connections")
def email_connections(
    request: Request,
    user: CurrentUser = Depends(require_consultant),
) -> dict[str, Any]:
    """Configured mailboxes for the Email node's connection picker.

    Names, providers and whether sending is permitted — never credentials. A
    workflow references a connection by id, so this is the whole surface the
    Builder needs.
    """
    del user
    service = _services(request).get("email")
    if service is None:
        return {"connections": [], "configured": False}
    return {
        "connections": service.describe_connections(),
        "configured": bool(service.connections),
    }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

#: Prefixes that hold what an author actually authored, as opposed to the
#: runtime fields every node of that type carries.
_AUTHORED_PREFIXES = ("result.", "data.", "decisions.", "messages.", "message.")


def _contract_sort_key(field: Any) -> tuple[int, str]:
    """Order a step's contract so the author's own fields come first.

    Sorted purely alphabetically, `attempts`, `error` and `model_used` sit above
    `result.intent` — so the Outputs tab opens on plumbing rather than on the
    schema the author just built. Runtime fields are real and stay listed; they
    are simply not the answer to "what does this step give me?".
    """
    path = field.path
    authored = 0 if path.startswith(_AUTHORED_PREFIXES) or path in ("result", "data", "decisions") else 1
    return (authored, path)


def _parse_workflow(yaml_text: str) -> WorkflowSpec:
    try:
        return load_workflow_from_string(yaml_text)
    except Exception as error:
        raise HTTPException(
            status_code=400, detail=f"Invalid workflow YAML: {error}"
        ) from error


def _require_llm(request: Request) -> Any:
    llm = _services(request).get("llm")
    if llm is None:
        raise HTTPException(
            status_code=503,
            detail="No LLM gateway is configured, so suggestions are unavailable.",
        )
    return llm


def _label_of(node: Any) -> str:
    """The business label if the author set one, else the technical id (§17)."""
    experience = getattr(node, "experience", None)
    display_name = getattr(experience, "display_name", None) if experience else None
    return display_name or node.id


def _execution_kind(type_name: str) -> str:
    from app.nodes.categories import execution_kind_for

    try:
        klass = NodeRegistry.get(type_name)
    except Exception:
        return "deterministic"
    declared = klass.__dict__.get("execution_kind")
    if declared:
        return str(declared)
    try:
        uses_llm = "llm" in klass.required_services({})
    except Exception:
        uses_llm = False
    return execution_kind_for(type_name, uses_llm=uses_llm)


def _is_external_write(type_name: str, config: dict[str, Any]) -> bool:
    if type_name != "EmailAgent":
        return False
    from app.integrations.email.base import SIDE_EFFECT_OPERATIONS

    return config.get("operation") in SIDE_EFFECT_OPERATIONS


def _ancestors(spec: WorkflowSpec, node_id: str) -> set[str]:
    reverse: dict[str, set[str]] = {node.id: set() for node in spec.nodes}
    for edge in spec.edges:
        targets = (
            edge.to
            if isinstance(edge.to, list)
            else ([edge.to] if edge.to else [])
        )
        for target in [*targets, *(edge.branches or {}).values()]:
            if target in reverse:
                reverse[target].add(edge.from_)

    found = {node_id}
    queue = list(reverse.get(node_id, set()))
    while queue:
        current = queue.pop()
        if current in found:
            continue
        found.add(current)
        queue.extend(reverse.get(current, set()))
    return found


def _slice_through(spec: WorkflowSpec, node_id: str) -> WorkflowSpec:
    """The smallest valid upstream slice ending at `node_id` (§22).

    A copy: the caller's spec — and therefore the saved workflow — is never
    touched.
    """
    if node_id not in {node.id for node in spec.nodes}:
        raise HTTPException(
            status_code=422, detail=f"Unknown step {node_id!r}"
        )

    keep = _ancestors(spec, node_id)
    kept_nodes = [node for node in spec.nodes if node.id in keep]

    edges = []
    for edge in spec.edges:
        if edge.from_ not in keep:
            continue
        if edge.branches:
            branches = {
                label: target
                for label, target in edge.branches.items()
                if target in keep
            }
            if branches:
                edges.append(
                    edge.model_copy(update={"branches": branches, "to": None})
                )
            continue
        targets = (
            [target for target in edge.to if target in keep]
            if isinstance(edge.to, list)
            else ([edge.to] if edge.to in keep else [])
        )
        if targets:
            edges.append(
                edge.model_copy(
                    update={"to": targets[0] if len(targets) == 1 else targets}
                )
            )

    return spec.model_copy(
        update={
            "nodes": kept_nodes,
            "edges": edges,
            "entry": spec.entry if spec.entry in keep else kept_nodes[0].id,
            "exit": node_id,
            "output": None,
        }
    )


def _short(value: Any, limit: int = 80) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _previewable(value: Any, depth: int = 0) -> Any:
    """Trim large values before they cross the wire.

    A simulation trace is rendered in a panel, not archived — a 200 kB extracted
    document body would make the response slow and the panel useless.
    """
    if depth > 8:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + "…"
    if isinstance(value, dict):
        return {
            key: _previewable(item, depth + 1)
            for key, item in list(value.items())[:60]
        }
    if isinstance(value, (list, tuple)):
        trimmed = [_previewable(item, depth + 1) for item in list(value)[:60]]
        if len(value) > 60:
            trimmed.append(f"… {len(value) - 60} more")
        return trimmed
    return value
