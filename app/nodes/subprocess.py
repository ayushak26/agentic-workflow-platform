"""SubprocessAgent — call another saved workflow as a reusable business
subprocess.

The child runs as a genuinely independent, top-level run — not nested inside
this run's own graph invocation. A nested `run_workflow` call was tried and
rejected: a child `HumanInLoopAgent` pause does not propagate up through a
nested `graph.ainvoke()`, among other problems (services-dict scoping,
thread_id collision, double run-history bookkeeping, recursive preflight
blowup). Instead:

    1. Launch  — resolve this step's inputs into the child's own declared
       workflow inputs, launch it via the same BackgroundRunManager path
       `POST /workflows/run` uses, and record the parent<->child
       correlation (app.workflow.subprocess_launches).
    2. Pause   — park this node with LangGraph's own interrupt(), exactly
       like HumanInLoopAgent does, reusing the platform's existing generic
       cooperative pause/resume machinery rather than inventing new
       plumbing.
    3. Resume  — once the child finishes (through its own, completely
       ordinary finalize path — see app.workflow.orchestration and
       app.workflow.subprocess_callback), a decision is delivered back
       through `resume_workflow_durable`, the exact function an approved
       HITL gate already resumes through.

Unlike HumanInLoopAgent, this node has a genuine side effect — launching the
child — *before* its own `interrupt()` call, and every resume path (in-process
`Command(resume=...)`, a recompiled persistent-checkpointer resume, or a
Mongo-fallback replay) re-runs this node's function from the top, not just
from the interrupt() line. So the launch decision cannot be "did
hitl_resume_decisions already have my answer" (that only covers the
Mongo-fallback case) — it has to be "does a durable launch record for this
exact step already exist", checked first and unconditionally, every time.
Delivery marks that record "delivered" rather than deleting it (see
app.workflow.subprocess_launches' module docstring for the real bug that
distinction fixes), so a re-entry after delivery finds its answer already
sitting there and skips pausing again entirely.
"""
from __future__ import annotations

import uuid
from typing import Any, ClassVar, Literal

from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger

log = get_logger(__name__)


class SubprocessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The referenced workflow's file name (without .yaml), same charset
    #: WorkflowBuilderStore already enforces for any saved workflow name.
    workflow: str = Field(description="Which saved workflow to run as a subprocess.")
    #: Explicit input mapping. Any child-declared input NOT given here falls
    #: back to a same-named parent workflow input, then a same-named parent
    #: node's whole output, then None — see _resolve_child_inputs. `Any`,
    #: not `str`: the compiler has already template-resolved every value by
    #: the time this config is validated, so a mapped
    #: `{{outputs.extract.parsed}}` arrives as a real dict, not a string.
    inputs: dict[str, Any] = Field(default_factory=dict)
    result_from: Literal["workflow_output", "node", "all_outputs"] = Field(
        default="workflow_output",
        description=(
            "workflow_output: the child's own declared output: contract. "
            "node: one specific node's raw output (set result_node). "
            "all_outputs: every node's raw output, keyed by node id."
        ),
    )
    result_node: str | None = Field(
        default=None,
        description="Which child node's output to use, when result_from is 'node'.",
    )
    #: How long the parent is willing to wait before giving up. Enforced by
    #: a lightweight watchdog, not by this node itself — a paused node has no
    #: code running to enforce its own deadline.
    timeout_seconds: float = Field(default=1800.0, gt=0, le=86400)


class SubprocessInput(BaseModel):
    pass


class SubprocessOutput(BaseModel):
    status: Literal["completed"] = "completed"
    result: Any = None
    child_run_id: str = ""
    child_workflow: str = ""


@NodeRegistry.register
class SubprocessAgent(NodeType):
    type_name = "SubprocessAgent"
    description = (
        "Run another saved workflow as a reusable business subprocess. It "
        "runs as its own independent run — this step waits for it to finish."
    )
    input_schema = SubprocessInput
    output_schema = SubprocessOutput
    config_schema = SubprocessConfig

    family: ClassVar[str] = "specialized"
    execution_kind: ClassVar[str] = "deterministic"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Launches another saved workflow as a fully independent run, "
            "with this step's data becoming that workflow's own inputs, and "
            "waits for it to finish before continuing."
        ),
        "why": (
            "A business process that is itself a reusable unit — quoting, "
            "onboarding, an approval chain — should be authored once and "
            "called from anywhere, the same way a business would delegate "
            "it to another team rather than re-explain it every time."
        ),
        "receives": "Input values, usually mapped from earlier steps, that become the child workflow's own inputs.",
        "produces": "result (the child's output, shaped by result_from), plus child_run_id for traceability.",
        "uses_ai": False,
        "external_action": False,
        "safety": (
            "The child is a normal run with its own normal preflight, own "
            "Cockpit visibility, and own audit trail — nothing about it is "
            "hidden inside this step."
        ),
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"audit_db", "background_run_manager"}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = SubprocessConfig(**resolved_config)
        launch = await self._get_or_create_launch(cfg, state)

        if launch["status"] == "delivered":
            # The child already finished — this is a re-entry (an in-process
            # Command(resume=...) re-runs this function from the top, same
            # as a durable Mongo-fallback replay would) after delivery
            # already happened. The result is sitting right here; there is
            # nothing left to pause for.
            decision = launch["delivered_decision"]
        else:
            decision = interrupt({
                "kind": "subprocess_pause",
                "node_id": self.node_id,
                "child_run_id": launch["child_run_id"],
                "child_workflow": cfg.workflow,
            })

        return self._finalize(cfg, decision)

    async def _get_or_create_launch(
        self, cfg: SubprocessConfig, state: dict[str, Any],
    ) -> dict[str, Any]:
        db = self.services.get("audit_db")
        run_manager = self.services.get("background_run_manager")
        if db is None or run_manager is None:
            raise RuntimeError(
                f"SubprocessAgent '{self.node_id}' needs the audit_db and "
                "background_run_manager services, which should always be "
                "configured in this deployment."
            )

        inputs = state.get("inputs") or {}
        parent_run_id = str(inputs.get("SYSTEM.run_id") or "")
        parent_session_id = str(state.get("session_id") or "")
        collection_id = str(state.get("collection_id") or "default")

        from app.workflow import subprocess_launches

        # Deterministic, not a fresh token: this is what makes every re-entry
        # of this node function — whatever caused it — find the SAME launch
        # record instead of starting a second child. Checked first and
        # separately from the reserve-below, so a re-entry after delivery
        # skips the depth guard, the child file load, and the input
        # resolution entirely — none of that is relevant once the result is
        # already known.
        launch_key = f"{parent_run_id}:{self.node_id}"
        existing = await subprocess_launches.find_by_launch_key(db, launch_key)
        if existing is not None:
            return existing

        # A real static cycle is already caught by preflight (see
        # _check_subprocess_agents/_find_subprocess_cycle) — this is the
        # runtime backstop for the cases preflight cannot see: the
        # referenced workflow didn't exist yet when this one was authored,
        # or two workflows were made mutually recursive after the fact.
        from app.config import settings

        depth = int(self.services.get("subprocess_depth") or 0)
        if depth >= settings.subprocess_max_depth:
            raise RuntimeError(
                f"SubprocessAgent '{self.node_id}' would nest subprocess "
                f"calls past the configured limit "
                f"({settings.subprocess_max_depth}) — check for a "
                "recursive reference between workflows."
            )

        child_spec, child_yaml = _load_child_workflow(cfg.workflow)
        child_inputs = _resolve_child_inputs(cfg, child_spec, state)
        child_run_id = str(uuid.uuid4())

        doc, created = await subprocess_launches.reserve_launch(
            db,
            launch_key=launch_key,
            parent_run_id=parent_run_id,
            parent_node_id=self.node_id,
            parent_session_id=parent_session_id,
            child_run_id=child_run_id,
            child_workflow=cfg.workflow,
            result_from=cfg.result_from,
            result_node=cfg.result_node,
            timeout_seconds=cfg.timeout_seconds,
        )
        if not created:
            # A racing concurrent entry already claimed this launch_key.
            return doc

        from app.runtime.executor import run_workflow
        from app.workflow.orchestration import start_new_run_record

        await start_new_run_record(
            db,
            run_id=doc["child_run_id"],
            session=parent_session_id,
            spec=child_spec,
            workflow_yaml=child_yaml,
            inputs=child_inputs,
            collection_id=collection_id,
        )
        # The child's own node instances see one more level of depth than
        # this one did — the thing that actually makes the runtime backstop
        # a chain-length counter rather than a single-hop check.
        child_services = {**self.services, "subprocess_depth": depth + 1}

        # Fire-and-forget: this node pauses right after this call, so the
        # child's own progress and terminal result are observed the exact
        # same way a person-started run's are — Run History and the SSE
        # event bus — never awaited inline here.
        run_manager.launch(
            run_workflow(
                child_spec,
                child_inputs,
                parent_session_id,
                collection_id=collection_id,
                services=child_services,
                run_id=doc["child_run_id"],
            ),
            db=db,
            run_id=doc["child_run_id"],
            session=parent_session_id,
            services=child_services,
        )
        log.info(
            "subprocess.launched",
            node_id=self.node_id,
            parent_run_id=parent_run_id,
            child_run_id=doc["child_run_id"],
            child_workflow=cfg.workflow,
        )
        return doc

    def _finalize(self, cfg: SubprocessConfig, decision: dict[str, Any]) -> dict[str, Any]:
        status = decision.get("status")
        if status == "rejected":
            # A rejected HITL gate *inside* the child (sp04_approval_gate,
            # sp05_response_preparation, ...) is not this node's own failure —
            # it is exactly the "approve/reject/edit, for the parent to act
            # on" contract those subprocesses document. Surfacing it as a
            # normal completed step, with the decision nested under `result`
            # (never a top-level `decision` key), keeps the platform's
            # generic "any node output with a top-level decision == reject
            # marks the whole run rejected" convention (app.runtime.hitl/
            # executor's _find_rejection) from firing here — the parent
            # workflow's own router/decision logic is what gets to decide
            # what a rejected subprocess means, the same way it already
            # decides what an MCPToolAgent write's declined approval means.
            return {
                "status": "completed",
                "result": {"decision": "reject", "reason": decision.get("error")},
                "child_run_id": decision.get("child_run_id") or "",
                "child_workflow": decision.get("child_workflow") or cfg.workflow,
            }
        if status != "completed":
            raise RuntimeError(
                f"SubprocessAgent '{self.node_id}' child workflow "
                f"{decision.get('child_workflow') or cfg.workflow!r} "
                f"{status or 'did not complete'}: "
                f"{decision.get('error') or 'no result was recorded'}"
            )
        return {
            "status": "completed",
            "result": decision.get("result"),
            "child_run_id": decision.get("child_run_id") or "",
            "child_workflow": decision.get("child_workflow") or cfg.workflow,
        }


def _load_child_workflow(name: str):
    from pathlib import Path

    from app.runtime.loader import load_workflow_from_string
    from app.workflow.builder_store import WorkflowBuilderStore

    WorkflowBuilderStore.validate_name(name)
    path = Path("workflows") / f"{name}.yaml"
    if not path.exists():
        raise RuntimeError(
            f"Subprocess workflow {name!r} does not exist at {path}."
        )
    yaml_text = path.read_text()
    return load_workflow_from_string(yaml_text), yaml_text


def _resolve_child_inputs(
    cfg: SubprocessConfig,
    child_spec: Any,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Explicit mapping -> same-named parent workflow input -> same-named
    parent node's whole output -> None.

    Mirrors app.runtime.pipeline_executor.materialize_stage_inputs, which
    solves the identical problem for a pipeline stage — reused, not
    reinvented, including its {raw, parsed} envelope-unwrap convention for
    json-typed inputs (_coerce_for_target).
    """
    from app.runtime.pipeline_executor import _coerce_for_target

    explicit = cfg.inputs
    parent_inputs = state.get("inputs") or {}
    parent_node_outputs = state.get("node_outputs") or {}

    resolved: dict[str, Any] = {}
    for name, input_spec in child_spec.inputs.items():
        if name in explicit:
            value = explicit[name]
        elif name in parent_inputs:
            value = parent_inputs[name]
        elif name in parent_node_outputs:
            value = parent_node_outputs[name]
        else:
            value = None
        resolved[name] = _coerce_for_target(value, input_spec)
    return resolved
