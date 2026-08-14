"""Business Projection: a pure, read-only reshaping of a run into business language.

    Run document (run_history.py)  +  Workflow spec (experience metadata)
                    │
                    ▼
          build_business_projection()
                    │
                    ▼
              Business View

No new store. Every field here is derived from data that already exists —
the run document's ``node_runs``/``outputs`` and the workflow's optional
``experience`` metadata (schema.py). This module has no I/O: callers (the
API route) fetch the run and gate, this function only reshapes them.

Stage bucketing (DEFAULT_STAGES, the matcher heuristics, humanize_identifier)
is a direct port of ui/src/modes/studio/guided/runtime-model.ts — the same
business-language grouping Guided Run used, now computed once on the server
so Business View and any future consumer see identical labels.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.runtime.schema import GuidedStageSpec, NodeSpec, WorkflowSpec
from app.workflow.fact_corrections import EDITABLE_FIELDS
from app.workflow.fact_corrections import NODE_ID as _FACT_CORRECTION_NODE_ID

_TERMINAL_NODE_STATUSES = {"done", "reused", "failed", "skipped", "cancelled"}

#: Business status a paused/running/etc. node maps to for display — mirrors
#: guided/runtime-model.ts's GUIDED_STATUS_LABEL, since this is the same
#: vocabulary Business View inherits from Guided Run.
_NODE_STATUS_LABEL = {
    "pending": "Planned",
    "active": "Working",
    "done": "Completed",
    "reused": "Completed from saved work",
    "paused": "Waiting for you",
    "failed": "Needs attention",
    "skipped": "Not needed",
    "cancelled": "Stopped safely",
}

#: Overall Work Item status shown in the header — the generic status model
#: is a known future gap (Phase 0 §06); this is the coarser version that
#: maps cleanly off today's five run statuses plus gate/pause_kind.
_RUN_STATUS_LABEL = {
    "running": "In Progress",
    "resuming": "In Progress",
    "completed": "Completed",
    "failed": "Needs Attention",
    "rejected": "Stopped",
}


@dataclass
class _StageDef:
    id: str
    display_name: str
    purpose: str = ""
    expected_output: str | None = None
    success_criteria: list[str] = field(default_factory=list)
    visibility: str = "standard"
    weight: float = 1.0
    matcher: re.Pattern[str] | None = None


# Same six stages and matchers as guided/runtime-model.ts's DEFAULT_STAGES,
# tested in the same precedence order (specific terms before generic ones).
_DEFAULT_STAGES: list[_StageDef] = [
    _StageDef(
        id="prepare", display_name="Prepare",
        purpose="Check the files, inputs and settings needed for a reliable run.",
        expected_output="A ready-to-use set of inputs and assumptions.",
        matcher=re.compile(r"(^|_)(start|load|input|ingest|preflight|normalise|normalize|metadata|readiness)(_|$)", re.I),
    ),
    _StageDef(
        id="understand", display_name="Understand",
        purpose="Interpret the request, objectives, constraints and success criteria.",
        expected_output="A shared understanding of what the final result must achieve.",
        matcher=re.compile(r"(understand|interpret|requirement|call_intelligence|research_plan|scope|objective|brief)", re.I),
    ),
    _StageDef(
        id="gather", display_name="Gather",
        purpose="Find and organise the evidence, data and prior work needed for the result.",
        expected_output="A traceable evidence and source set for later work.",
        matcher=re.compile(r"(retriev|search|research|source|evidence|citation|dataset|database|literature|candidate)", re.I),
    ),
    _StageDef(
        id="create", display_name="Create",
        purpose="Produce the analysis, draft, plan, figures or other main deliverable.",
        expected_output="A complete working version of the requested deliverable.",
        matcher=re.compile(r"(draft|create|generate|compile|synthesi|methodology|blueprint|concept|figure|assemble)", re.I),
    ),
    _StageDef(
        id="check", display_name="Check",
        purpose="Review completeness, consistency, evidence coverage and quality.",
        expected_output="A checked result with gaps and review items clearly identified.",
        matcher=re.compile(r"(verify|review|evaluat|quality|consisten|coverage|red_team|peer_review|compliance|gate|truth_graph)", re.I),
    ),
    _StageDef(
        id="finalise", display_name="Finalise",
        purpose="Package the approved work and prepare the final deliverables.",
        expected_output="The final deliverable and its supporting files.",
        matcher=re.compile(r"(final|submission|render|package|export|docx|pdf|publish)", re.I),
    ),
]
_STAGE_PRECEDENCE = ["finalise", "check", "prepare", "understand", "gather", "create"]
_DEFAULT_STAGE_BY_ID = {stage.id: stage for stage in _DEFAULT_STAGES}

#: Node type_names treated as "a check against a trusted system" for the
#: §31 "What I checked" list — heuristic, not a platform contract, since no
#: unified tool architecture / capability layer exists yet (Phase 0 §04).
_CHECK_NODE_TYPES = {"MCPToolAgent", "MCPAgent", "RAGAgent", "KnowledgeRetrievalAgent"}

#: Node type_name that carries a deterministic business decision.
_DECISION_NODE_TYPE = "DecisionAgent"


def humanize_identifier(value: str) -> str:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    spaced = re.sub(r"[_./-]+", " ", spaced)
    spaced = re.sub(r"\b(agent|node)\b", "", spaced, flags=re.I)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    if not spaced:
        return "Workflow step"
    return spaced[0].upper() + spaced[1:]


def _inferred_stage_id(node_id: str, type_name: str, index: int, total: int) -> str:
    searchable = f"{node_id} {type_name}"
    for stage_id in _STAGE_PRECEDENCE:
        stage = _DEFAULT_STAGE_BY_ID[stage_id]
        if stage.matcher and stage.matcher.search(searchable):
            return stage_id
    fallback_index = min(
        len(_DEFAULT_STAGES) - 1,
        int((index / max(1, total)) * len(_DEFAULT_STAGES)),
    )
    return _DEFAULT_STAGES[fallback_index].id


def _stage_for_node(
    node: NodeSpec, explicit_stages: list[GuidedStageSpec], index: int, total: int,
) -> str:
    experience = node.experience
    if experience is not None and experience.stage_id:
        return experience.stage_id
    for stage in explicit_stages:
        if node.id in stage.node_ids:
            return stage.id
    return _inferred_stage_id(node.id, node.type, index, total)


def _stage_state(node_ids: list[str], statuses: dict[str, str]) -> str:
    values = [statuses.get(node_id, "pending") for node_id in node_ids]
    if any(status == "failed" for status in values):
        return "attention"
    if any(status in ("active", "paused") for status in values):
        return "active"
    if values and all(status in _TERMINAL_NODE_STATUSES for status in values):
        if all(status in ("skipped", "cancelled") for status in values):
            return "skipped"
        return "completed"
    return "planned"


def _compact_text(value: Any, max_length: int = 180) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return None
    return text if len(text) <= max_length else f"{text[: max_length - 1]}…"


def _key_points_from_output(output: Any) -> list[str]:
    if output is None:
        return []
    if isinstance(output, list):
        return [f"Produced {len(output)} item{'' if len(output) == 1 else 's'}."]
    if not isinstance(output, dict):
        text = _compact_text(output)
        return [text] if text else []

    points: list[str] = []
    for key in ("outcome", "summary", "answer", "result", "raw"):
        text = _compact_text(output.get(key))
        if text:
            points.append(text)
            break
    for key, value in output.items():
        if len(points) >= 3:
            break
        if key in ("outcome", "summary", "answer", "result", "raw"):
            continue
        label = humanize_identifier(key)
        if isinstance(value, list) and value:
            points.append(f"{label}: {len(value)} item{'' if len(value) == 1 else 's'}.")
        elif isinstance(value, bool):
            points.append(f"{label}: {'yes' if value else 'no'}.")
        elif isinstance(value, (int, float)):
            points.append(f"{label}: {value:,}.")
    if not points and output:
        points.append(f"Produced {len(output)} structured field{'' if len(output) == 1 else 's'}.")
    return points[:3]


def _fallback_experience(node_id: str, stage: _StageDef) -> dict[str, str]:
    return {
        "display_name": humanize_identifier(node_id),
        "purpose": stage.purpose or f"Complete the {stage.display_name.lower()} work for this workflow.",
        "expected_output": stage.expected_output or "A structured result for the next step.",
    }


def _node_business_status(node_id: str, run_doc: dict[str, Any]) -> str:
    node_run = (run_doc.get("node_runs") or {}).get(node_id) or {}
    live_status = node_run.get("status")
    mapped = {
        "running": "active", "paused": "paused", "completed": "done",
        "reused": "reused", "failed": "failed",
    }.get(live_status)
    if mapped:
        return mapped
    if node_id in (run_doc.get("outputs") or {}):
        return "done"
    if run_doc.get("status") in ("completed", "failed", "rejected"):
        return "skipped"
    return "pending"


def _timestamp_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def build_business_projection(
    run_doc: dict[str, Any],
    *,
    workflow_spec: WorkflowSpec | None,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reshape one run into the Business View's shape (§118).

    ``workflow_spec`` is optional — a run whose ``workflow_yaml`` fails to
    parse (or is absent) still gets a usable, if less-labeled, projection
    rather than a 500: everything degrades to node-id-derived labels.
    """
    run_id = run_doc.get("run_id", "")
    run_status = run_doc.get("status", "unknown")
    node_runs: dict[str, Any] = run_doc.get("node_runs") or {}
    outputs: dict[str, Any] = run_doc.get("outputs") or {}
    node_types: dict[str, str] = run_doc.get("node_types") or {}

    nodes: list[NodeSpec] = list(workflow_spec.nodes) if workflow_spec is not None else []
    explicit_stages = (
        workflow_spec.experience.stages
        if workflow_spec is not None and workflow_spec.experience is not None
        else []
    )
    stage_defs: dict[str, _StageDef] = dict(_DEFAULT_STAGE_BY_ID)
    for stage in explicit_stages:
        stage_defs[stage.id] = _StageDef(
            id=stage.id, display_name=stage.display_name, purpose=stage.purpose,
            expected_output=stage.expected_output, success_criteria=list(stage.success_criteria),
            visibility=stage.visibility, weight=stage.weight,
        )

    statuses = {node.id: _node_business_status(node.id, run_doc) for node in nodes}
    stage_node_ids: dict[str, list[str]] = {}
    node_stage: dict[str, str] = {}
    total = len(nodes)
    for index, node in enumerate(nodes):
        stage_id = _stage_for_node(node, explicit_stages, index, total)
        node_stage[node.id] = stage_id
        stage_node_ids.setdefault(stage_id, []).append(node.id)
        stage_defs.setdefault(stage_id, _StageDef(id=stage_id, display_name=humanize_identifier(stage_id)))

    ordered_stage_ids: list[str] = []
    for stage_id in (
        [s.id for s in explicit_stages] + [s.id for s in _DEFAULT_STAGES] + list(stage_node_ids)
    ):
        if stage_id not in ordered_stage_ids and stage_node_ids.get(stage_id):
            ordered_stage_ids.append(stage_id)

    progress: list[dict[str, Any]] = []
    for stage_id in ordered_stage_ids:
        node_ids = stage_node_ids.get(stage_id, [])
        state = _stage_state(node_ids, statuses)
        definition = stage_defs[stage_id]
        completed = sum(1 for nid in node_ids if statuses.get(nid) in ("done", "reused"))
        progress.append({
            "id": stage_id,
            "display_name": definition.display_name,
            "state": state,  # planned | active | completed | attention | skipped
            "completed_count": completed,
            "total_count": len(node_ids),
        })

    # ---- current activity -------------------------------------------------
    active_node_id = next((nid for nid, s in statuses.items() if s == "active"), None)
    paused_node_id = next((nid for nid, s in statuses.items() if s == "paused"), None)
    focus_node_id = paused_node_id or active_node_id
    current_activity: dict[str, Any] | None = None
    if focus_node_id is not None:
        node = next((n for n in nodes if n.id == focus_node_id), None)
        experience = node.experience if node is not None else None
        stage = stage_defs.get(node_stage.get(focus_node_id, ""), _DEFAULT_STAGE_BY_ID["prepare"])
        fallback = _fallback_experience(focus_node_id, stage)
        message = None
        if experience is not None:
            message = (
                experience.running_message if focus_node_id == active_node_id
                else None
            ) or experience.purpose
        current_activity = {
            "node_id": focus_node_id,
            "display_name": (experience.display_name if experience else None) or fallback["display_name"],
            "message": message or fallback["purpose"],
            "waiting_for_you": focus_node_id == paused_node_id,
        }

    # ---- what I understood (first completed 'understand'-stage node) -----
    understanding: dict[str, Any] = {}
    for node in nodes:
        if node_stage.get(node.id) != "understand":
            continue
        if statuses.get(node.id) not in ("done", "reused"):
            continue
        output = outputs.get(node.id)
        if isinstance(output, dict):
            understanding = {
                "node_id": node.id,
                "result": output.get("result", output),
                "confidence": output.get("confidence"),
            }
            break

    # ---- editable facts (§ fact correction — only for the one workflow
    # app/workflow/fact_corrections.py knows the rule dependencies of) -------
    editable_facts: list[str] = []
    if understanding.get("node_id") == _FACT_CORRECTION_NODE_ID and isinstance(understanding.get("result"), dict):
        editable_facts = sorted(EDITABLE_FIELDS & understanding["result"].keys())
    stale_decisions: list[str] = list(dict.fromkeys(run_doc.get("stale_decisions") or []))

    # ---- missing information (generic scan; never fabricated) -------------
    missing_information: list[str] = []
    seen_missing: set[str] = set()
    for output in outputs.values():
        result = output.get("result") if isinstance(output, dict) else None
        candidates = (result or {}).get("missing_information") if isinstance(result, dict) else None
        if isinstance(candidates, list):
            for item in candidates:
                if isinstance(item, str) and item not in seen_missing:
                    seen_missing.add(item)
                    missing_information.append(item)

    # ---- what I checked -----------------------------------------------------
    checks: list[dict[str, Any]] = []
    for node in nodes:
        if node_types.get(node.id, node.type) not in _CHECK_NODE_TYPES:
            continue
        status = statuses.get(node.id, "pending")
        experience = node.experience
        output = outputs.get(node.id)
        points = _key_points_from_output(output)
        checks.append({
            "node_id": node.id,
            "display_name": (experience.display_name if experience else None) or humanize_identifier(node.id),
            "status": status,
            "status_label": _NODE_STATUS_LABEL.get(status, status),
            "outcome": points[0] if points else None,
        })

    # ---- decision + why (first completed DecisionAgent) --------------------
    decision: dict[str, Any] | None = None
    decision_explanation: list[dict[str, Any]] = []
    for node in nodes:
        if node_types.get(node.id, node.type) != _DECISION_NODE_TYPE:
            continue
        if statuses.get(node.id) not in ("done", "reused"):
            continue
        output = outputs.get(node.id)
        if not isinstance(output, dict):
            continue
        decision = {
            "node_id": node.id,
            "decisions": output.get("decisions", {}),
            "rules_triggered": output.get("matched_rules", []),
            "summary": output.get("summary", []),
        }
        for rule in output.get("explanation", []) or []:
            if isinstance(rule, dict) and rule.get("matched"):
                decision_explanation.append({
                    "name": rule.get("name", ""),
                    "description": rule.get("description", ""),
                })
        break

    # ---- required user actions / allowed controls --------------------------
    required_user_actions: list[dict[str, Any]] = []
    if gate is not None and gate.get("paused"):
        if gate.get("pause_kind") == "user_requested":
            required_user_actions.append({
                "type": "resume_decision", "node_id": gate.get("node_id"),
                "message": "This work is paused. Resume it when you're ready.",
            })
        else:
            required_user_actions.append({
                "type": "approval_review",
                "node_id": gate.get("node_id"),
                "question": gate.get("question", ""),
                "allowed_actions": gate.get("allowed_actions") or ["approve", "reject"],
            })

    if run_status in ("running", "resuming"):
        allowed_controls = ["pause", "stop"]
    elif run_status == "paused" and gate and gate.get("pause_kind") == "user_requested":
        allowed_controls = ["resume", "stop"]
    elif run_status == "paused":
        allowed_controls = list(dict.fromkeys(
            (gate or {}).get("allowed_actions", ["approve", "reject"]) + ["ask_why", "stop"]
        ))
    elif run_status == "failed":
        allowed_controls = ["retry", "stop"]
    else:
        allowed_controls = []

    # ---- timeline -----------------------------------------------------------
    timeline: list[dict[str, Any]] = []
    started_at = _timestamp_iso(run_doc.get("started_at"))
    if started_at:
        timeline.append({"ts": started_at, "label": "Request received"})
    for node_id, node_run in node_runs.items():
        experience = next((n.experience for n in nodes if n.id == node_id), None)
        display_name = (
            (experience.display_name if experience else None) or humanize_identifier(node_id)
        )
        node_status = node_run.get("status")
        node_started = _timestamp_iso(node_run.get("started_at"))
        node_ended = _timestamp_iso(node_run.get("ended_at"))
        if node_started:
            timeline.append({"ts": node_started, "label": f"{display_name} started"})
        if node_status in ("completed", "reused") and node_ended:
            message = (experience.completed_message if experience else None) or f"{display_name} completed"
            timeline.append({"ts": node_ended, "label": message})
        elif node_status == "failed" and node_ended:
            message = (experience.failure_message if experience else None) or f"{display_name} needs attention"
            timeline.append({"ts": node_ended, "label": message})
        elif node_status == "paused" and node_ended:
            timeline.append({"ts": node_ended, "label": f"Waiting for your review on {display_name.lower()}"})
    ended_at = _timestamp_iso(run_doc.get("ended_at"))
    if ended_at and run_status in ("completed", "failed", "rejected"):
        timeline.append({"ts": ended_at, "label": _RUN_STATUS_LABEL.get(run_status, run_status)})
    timeline.sort(key=lambda entry: entry["ts"] or "")

    goal = None
    if workflow_spec is not None and workflow_spec.experience is not None:
        goal = workflow_spec.experience.goal
    goal = goal or run_doc.get("workflow_name") or "Complete this request."

    return {
        "work_item": {
            "id": run_id,
            "type": humanize_identifier(run_doc.get("workflow_name", "")) or "Work Item",
            "status": _RUN_STATUS_LABEL.get(
                run_status,
                "Waiting for You" if required_user_actions else run_status,
            ),
            "started_at": started_at,
            "updated_at": _timestamp_iso(run_doc.get("updated_at")) or started_at,
            "assigned_to": run_doc.get("assigned_to"),
        },
        "process": {
            "name": run_doc.get("workflow_name", ""),
            "goal": goal,
        },
        "status": run_status,
        "current_activity": current_activity,
        "progress": progress,
        "understanding": understanding,
        "editable_facts": editable_facts,
        "stale_decisions": stale_decisions,
        "missing_information": missing_information,
        "checks": checks,
        # BusinessFact[] is a future platform primitive (Phase 0 §05) — left
        # empty rather than repurposing unrelated data to fill the shape.
        "facts": [],
        "decision": decision,
        "decision_explanation": decision_explanation,
        "uncertainties": [],
        "recommendations": [],
        "proposed_actions": [],
        "completed_actions": [],
        "required_user_actions": required_user_actions,
        "allowed_controls": allowed_controls,
        "timeline": timeline,
    }
