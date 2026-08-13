"""Zero-token validation of authored business logic.

Preflight already proves the graph compiles, the configs validate and the
template references resolve. What it could not check is the *logic* an author
builds visually: a rule comparing a list with `>=`, a threshold outside a field's
range, an enum value that isn't in the enum, a reference to a field the upstream
AI Task does not actually produce, or an email send with no human in front of it.

Those are all decidable from the workflow's own typed schemas, without a model
call and without executing anything — which is exactly why they belong here
rather than in a run that discovers them halfway through.

Every check needs the same thing first: a map of every dotted path the workflow
can produce, and what type each one is. `build_field_index` computes that once
from the node configs, and the checks read it.
"""
from __future__ import annotations

from difflib import get_close_matches
from types import UnionType
from typing import (
    Any,
    Callable,
    Iterable,
    Literal,
    Union,
    get_args,
    get_origin,
)

from pydantic import ValidationError

from app.nodes.registry import NodeRegistry
from app.runtime.field_schema import FieldPath, field_paths
from app.runtime.rules import (
    SET_OPERATORS,
    UNARY_OPERATORS,
    Condition,
    collect_conditions,
    operators_for_type,
)
from app.runtime.schema import NodeSpec, WorkflowSpec


#: Node types whose output contract is fully typed, i.e. built from a visual
#: field schema. A reference into one of these can be *proved* wrong; a reference
#: into a free-form dict output can only be guessed at, so those are skipped
#: rather than warned about.
_TYPED_OUTPUT_BUILDERS: dict[str, Callable[[dict[str, Any]], list[FieldPath]]] = {}


def _register_builders() -> None:
    """Populate the typed-output builders lazily.

    Imported inside the function because these node modules import the runtime,
    and doing it at module scope would make preflight and nodes circular.
    """
    if _TYPED_OUTPUT_BUILDERS:
        return

    from app.nodes.ai_task import effective_fields
    from app.nodes.decision import DecisionConfig
    from app.nodes.workflow_input import WorkflowInputConfig

    def ai_task_paths(config: dict[str, Any]) -> list[FieldPath]:
        return [
            path.model_copy(update={"path": f"result.{path.path}"})
            for path in field_paths(effective_fields(config))
        ]

    def input_paths(config: dict[str, Any]) -> list[FieldPath]:
        specs = WorkflowInputConfig(**config).as_field_specs()
        return [
            path.model_copy(update={"path": f"data.{path.path}"})
            for path in field_paths(specs)
        ]

    def decision_paths(config: dict[str, Any]) -> list[FieldPath]:
        parsed = DecisionConfig(**config)
        found: list[FieldPath] = []
        for name in sorted(parsed.output_field_names()):
            found.append(
                FieldPath(
                    path=f"decisions.{name}",
                    type=_infer_kind(_declared_value(parsed, name)),
                    description="Set by a business rule.",
                    required=name in parsed.defaults,
                    # A field only some rules set may legitimately be absent on
                    # the branches where no rule fired.
                    may_be_unavailable=name not in parsed.defaults,
                )
            )
        return found

    def transform_paths(config: dict[str, Any]) -> list[FieldPath]:
        from app.nodes.data_transform import DataTransformConfig

        parsed = DataTransformConfig(**config)
        return [
            FieldPath(
                path=f"data.{op.target}",
                type=_transform_kind(op.operation),
                description=op.description,
                required=True,
                may_be_unavailable=op.default is None,
            )
            for op in parsed.operations
        ]

    _TYPED_OUTPUT_BUILDERS.update(
        {
            "AITaskAgent": ai_task_paths,
            "WorkflowInputAgent": input_paths,
            "DecisionAgent": decision_paths,
            "DataTransformAgent": transform_paths,
        }
    )


def _declared_value(parsed: Any, name: str) -> Any:
    if name in parsed.defaults:
        return parsed.defaults[name]
    for rule in parsed.rules:
        for action in rule.then:
            if action.field == name:
                return action.value
    return None


def _infer_kind(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str):
        return "string"
    return "unknown"


_TRANSFORM_KINDS = {
    "count": "integer",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "select": "object",
    "split": "list",
    "format": "string",
    "join": "string",
    "lowercase": "string",
    "uppercase": "string",
    "trim": "string",
}


def _transform_kind(operation: str) -> str:
    return _TRANSFORM_KINDS.get(operation, "unknown")


_ANNOTATION_KINDS: list[tuple[type, str]] = [
    # bool before int: bool is a subclass of int, so the int arm would swallow it.
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "list"),
    (dict, "object"),
]


def _kind_from_annotation(annotation: Any) -> tuple[str, bool]:
    """Best-effort (kind, nullable) for a declared output_schema field.

    Node output schemas are ordinary Pydantic models, so their runtime fields
    (`status`, `confidence`, `message_count`) carry real types. Reading them
    means a rule against `outputs.step.confidence` — the field the AI Task
    *promotes*, not the one inside the author's schema — is type-checked and
    range-checked too, which is where the "0.80 written as 80" mistake actually
    lives.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        arms = [arm for arm in get_args(annotation) if arm is not type(None)]
        nullable = len(arms) != len(get_args(annotation))
        if len(arms) == 1:
            kind, _ = _kind_from_annotation(arms[0])
            return kind, nullable
        return "unknown", nullable
    if origin in (list, tuple, set, frozenset):
        return "list", False
    if origin is dict:
        return "object", False
    if origin is Literal:
        # A Literal over strings is an enum in everything but name — which is
        # what makes `status not_equals "okay"` catchable.
        values = get_args(annotation)
        if values and all(isinstance(value, str) for value in values):
            return "enum", False
        return "unknown", False
    if isinstance(annotation, type):
        for python_type, kind in _ANNOTATION_KINDS:
            if issubclass(annotation, python_type):
                return kind, False
    return "unknown", False


def _declared_output_paths(klass: Any) -> list[FieldPath]:
    """Typed paths for a node's own output_schema fields."""
    found: list[FieldPath] = []
    for name, field in klass.output_schema.model_fields.items():
        kind, nullable = _kind_from_annotation(field.annotation)
        enum_values: list[str] = []
        if kind == "enum":
            annotation = field.annotation
            if get_origin(annotation) in (Union, UnionType):
                annotation = next(
                    (
                        arm
                        for arm in get_args(annotation)
                        if get_origin(arm) is Literal
                    ),
                    annotation,
                )
            enum_values = [str(value) for value in get_args(annotation)]
        found.append(
            FieldPath(
                path=name,
                type=kind,
                description="",
                required=field.is_required(),
                nullable=nullable,
                enum_values=enum_values,
                may_be_unavailable=nullable,
            )
        )
    return found


# --------------------------------------------------------------------------
# Field index
# --------------------------------------------------------------------------

class FieldIndex:
    """Every value the workflow can produce, keyed by its reference path."""

    def __init__(self) -> None:
        self.by_path: dict[str, FieldPath] = {}
        #: node id → whether its output shape is fully typed. An untyped node
        #: (a free-form dict output) makes references *through* it unprovable,
        #: which is a reason to stay silent rather than to warn.
        self.typed_nodes: dict[str, bool] = {}

    def add(self, node_id: str, path: FieldPath) -> None:
        for prefix in (f"outputs.{node_id}", node_id):
            self.by_path[f"{prefix}.{path.path}"] = path

    def get(self, reference: str) -> FieldPath | None:
        return self.by_path.get(reference)

    def node_of(self, reference: str) -> str | None:
        parts = reference.split(".")
        if not parts:
            return None
        if parts[0] == "outputs" and len(parts) > 1:
            return parts[1]
        if parts[0] in self.typed_nodes:
            return parts[0]
        return None

    def paths_for(self, node_id: str) -> list[FieldPath]:
        prefix = f"outputs.{node_id}."
        return [
            value
            for key, value in self.by_path.items()
            if key.startswith(prefix)
        ]


def build_field_index(spec: WorkflowSpec) -> FieldIndex:
    """Typed paths for every node in the workflow.

    Also feeds the Builder's mapping picker and rule editor through
    /api/builder/output-contract, so the operators a user is offered and the
    references preflight authorises are computed from one place.
    """
    _register_builders()
    index = FieldIndex()

    for node in spec.nodes:
        builder = _TYPED_OUTPUT_BUILDERS.get(node.type)
        if builder is None:
            index.typed_nodes[node.id] = False
            # Still register the declared output_schema field names, untyped, so
            # a reference to a real field on a specialized node isn't reported
            # as unknown.
            try:
                klass = NodeRegistry.get(node.type)
            except Exception:
                continue
            for path in _declared_output_paths(klass):
                index.add(node.id, path)
            continue

        index.typed_nodes[node.id] = True
        try:
            paths = builder(node.effective_config())
        except (ValidationError, ValueError):
            # Config validation reports this properly elsewhere; treat the node
            # as untyped for the purposes of these checks.
            index.typed_nodes[node.id] = False
            continue

        # Declared runtime fields first, then the visual schema's paths: the
        # two never collide (one is `status`, the other `result.intent`), but
        # ordering makes the precedence explicit if they ever do.
        try:
            for path in _declared_output_paths(NodeRegistry.get(node.type)):
                index.add(node.id, path)
        except Exception:
            pass
        for path in paths:
            index.add(node.id, path)

    return index


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def validate_business_logic(
    spec: WorkflowSpec,
    *,
    issue: Callable[..., None],
    guaranteed_before: Callable[[str, str], bool] | None = None,
) -> None:
    """Run every logic check. `issue` is preflight's own issue recorder.

    `guaranteed_before(a, b)` answers "does node `a` always run before node
    `b`?" — passed in rather than recomputed, because preflight already builds
    the topology it needs for that.
    """
    index = build_field_index(spec)
    nodes = {node.id: node for node in spec.nodes}

    for node in spec.nodes:
        config = node.effective_config()
        if node.type == "DecisionAgent":
            _check_decision(node, config, index, nodes, issue, guaranteed_before)
        elif node.type == "RouterAgent":
            _check_router(node, config, index, nodes, issue, guaranteed_before)
        elif node.type == "AITaskAgent":
            _check_ai_task(node, config, issue)

    _check_conditional_join_reachability(spec, issue)
    _check_external_actions(spec, issue, guaranteed_before)


def _check_conditional_join_reachability(
    spec: WorkflowSpec,
    issue: Callable[..., None],
) -> None:
    """Two conditional dispatches into one shared step form an unsatisfiable join.

    The compiler treats every declared predecessor of a node as an AND-join: the
    node waits for *all* of them (see _wire_edges' join-gate logic, which exists
    to stop a faster branch firing a shared node before a paused one resumes).
    That is correct for parallel fan-in — and a trap for routers.

    The shape that bites, drawn from the obvious "two gates in a row" design:

        automation_gate ──false──▶ route_request ──technical_support──▶ support
              │                          │
              └──true──────────────┐     └──human_review──┐
                                   ▼                      ▼
                              human_review  ◀─────────────┘

    `human_review` now has two arrival groups. Only one branch of
    `automation_gate` fires per run, so the two groups can never both arrive, and
    the node simply never executes — the run reports **completed** while silently
    skipping the escalation it was built to guarantee. No error, no failed node,
    nothing in the trace: the single worst failure mode a business workflow can
    have.

    Detected structurally rather than by simulation, because a simulation only
    reveals it on the input that happens to take that branch.
    """
    routers = {
        edge.from_: edge
        for edge in spec.edges
        if edge.condition and edge.branches
    }
    if not routers:
        return

    forward: dict[str, set[str]] = {node.id: set() for node in spec.nodes}
    for edge in spec.edges:
        for target in _edge_targets(edge):
            if edge.from_ in forward:
                forward[edge.from_].add(target)

    # Which arrival groups feed each target, mirroring the compiler's own
    # grouping: one group per conditional dispatch, one combined group for all
    # plain edges.
    groups: dict[str, list[tuple[str, str]]] = {}
    for edge in spec.edges:
        if edge.condition and edge.branches:
            for label, target in edge.branches.items():
                groups.setdefault(target, []).append((edge.from_, label))
            continue
        for target in _edge_targets(edge):
            groups.setdefault(target, []).append(("__plain__", ""))

    for target, arrivals in groups.items():
        distinct = {source for source, _ in arrivals}
        if len(distinct) < 2:
            continue

        for router_id, edge in routers.items():
            if router_id not in distinct:
                continue
            # The branch this router uses to reach the shared target.
            own_labels = {
                label for label, dest in edge.branches.items() if dest == target
            }
            if not own_labels:
                continue
            # Any *other* source that is only reachable through a different
            # branch of this same router can never fire in the same run.
            for other in distinct - {router_id}:
                blocking = _reaching_branches(edge, other, forward)
                if blocking and not (blocking & own_labels):
                    issue(
                        "ROUTER_JOIN_UNREACHABLE",
                        (
                            f"Step {target!r} is reached both directly from "
                            f"{router_id!r} (branch {sorted(own_labels)}) and via "
                            f"{other!r}, which only runs on {router_id!r}'s "
                            f"{sorted(blocking)} branch. Only one branch runs per "
                            f"request, so {target!r} would wait forever for the "
                            "other and never execute — the run would report "
                            "completed while silently skipping it."
                        ),
                        path=f"nodes.{target}",
                        node_id=target,
                        suggestion=(
                            "Use one router with a case per outcome (conditions "
                            f"mode) instead of routing into {target!r} from two "
                            "places, or give each path its own step."
                        ),
                    )
                    break


def _reaching_branches(
    edge: Any, node_id: str, forward: dict[str, set[str]]
) -> set[str]:
    """Which of this router's branches can reach `node_id`."""
    found: set[str] = set()
    for label, target in edge.branches.items():
        if target == node_id or node_id in _reachable_from(target, forward):
            found.add(label)
    return found


def _reachable_from(start: str, forward: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    queue = [start]
    while queue:
        current = queue.pop()
        for nxt in forward.get(current, set()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _edge_targets(edge: Any) -> list[str]:
    targets: list[str] = []
    if isinstance(edge.to, list):
        targets.extend(edge.to)
    elif edge.to:
        targets.append(edge.to)
    targets.extend((edge.branches or {}).values())
    return targets


def _check_decision(
    node: NodeSpec,
    config: dict[str, Any],
    index: FieldIndex,
    nodes: dict[str, NodeSpec],
    issue: Callable[..., None],
    guaranteed_before: Callable[[str, str], bool] | None,
) -> None:
    from app.nodes.decision import DecisionConfig

    try:
        parsed = DecisionConfig(**config)
    except ValidationError:
        return

    for rule_index, rule in enumerate(parsed.rules):
        path = f"nodes.{node.id}.config.rules.{rule_index}"
        for condition in collect_conditions(rule.when):
            _check_condition(
                condition,
                node=node,
                index=index,
                nodes=nodes,
                path=path,
                issue=issue,
                guaranteed_before=guaranteed_before,
                label=f"rule {rule.name!r}",
            )


def _check_router(
    node: NodeSpec,
    config: dict[str, Any],
    index: FieldIndex,
    nodes: dict[str, NodeSpec],
    issue: Callable[..., None],
    guaranteed_before: Callable[[str, str], bool] | None,
) -> None:
    from app.nodes.router import RouterConfig

    try:
        parsed = RouterConfig(**config)
    except ValidationError:
        return

    if parsed.mode == "field" and parsed.route_field:
        described = _resolve_reference(
            parsed.route_field,
            node=node,
            index=index,
            nodes=nodes,
            path=f"nodes.{node.id}.config.route_field",
            issue=issue,
            guaranteed_before=guaranteed_before,
            label="the routing field",
        )
        if described is not None and described.enum_values:
            unknown = [
                value
                for value in parsed.branches
                if value.strip().lower()
                not in {item.strip().lower() for item in described.enum_values}
            ]
            if unknown:
                issue(
                    "INVALID_ENUM_VALUE",
                    (
                        f"Router branches on values {unknown} that "
                        f"{parsed.route_field} can never hold. Allowed values: "
                        f"{described.enum_values}."
                    ),
                    path=f"nodes.{node.id}.config.branches",
                    node_id=node.id,
                    suggestion=(
                        "Use one of the allowed values, or add the value to the "
                        "upstream AI Task's enum."
                    ),
                )
            uncovered = [
                value
                for value in described.enum_values
                if value.strip().lower()
                not in {item.strip().lower() for item in parsed.branches}
            ]
            if uncovered and not parsed.fallback:
                issue(
                    "MISSING_DEFAULT_ROUTE",
                    (
                        f"Values {uncovered} have no branch and there is no "
                        "fallback, so those requests would fail at the router."
                    ),
                    severity="warning",
                    path=f"nodes.{node.id}.config.branches",
                    node_id=node.id,
                    suggestion=(
                        "Add a branch for each value, or set a fallback branch."
                    ),
                )

    for case_index, case in enumerate(parsed.cases):
        for condition in collect_conditions(case.when):
            _check_condition(
                condition,
                node=node,
                index=index,
                nodes=nodes,
                path=f"nodes.{node.id}.config.cases.{case_index}",
                issue=issue,
                guaranteed_before=guaranteed_before,
                label=f"route {case.route!r}",
            )


def _check_condition(
    condition: Condition,
    *,
    node: NodeSpec,
    index: FieldIndex,
    nodes: dict[str, NodeSpec],
    path: str,
    issue: Callable[..., None],
    guaranteed_before: Callable[[str, str], bool] | None,
    label: str,
) -> None:
    described = _resolve_reference(
        condition.field,
        node=node,
        index=index,
        nodes=nodes,
        path=path,
        issue=issue,
        guaranteed_before=guaranteed_before,
        label=label,
    )
    if described is None:
        return

    allowed = operators_for_type(described.type)
    if described.type != "unknown" and condition.operator not in allowed:
        issue(
            "RULE_TYPE_MISMATCH",
            (
                f"{label}: operator {condition.operator!r} cannot apply to "
                f"{condition.field} ({described.type}). Valid operators: "
                f"{list(allowed)}."
            ),
            path=path,
            node_id=node.id,
            suggestion=(
                f"Choose an operator the Builder offers for a "
                f"{described.type} field."
            ),
        )
        return

    if condition.operator in UNARY_OPERATORS:
        return

    candidates = (
        list(condition.value)
        if condition.operator in SET_OPERATORS
        and isinstance(condition.value, (list, tuple))
        else [condition.value]
    )

    if described.enum_values and condition.operator in (
        "equals",
        "not_equals",
        "in",
        "not_in",
    ):
        allowed_values = {item.strip().lower() for item in described.enum_values}
        invalid = [
            value
            for value in candidates
            if isinstance(value, str) and value.strip().lower() not in allowed_values
        ]
        if invalid:
            issue(
                "INVALID_ENUM_VALUE",
                (
                    f"{label}: {condition.field} can never equal {invalid}. "
                    f"Allowed values: {described.enum_values}."
                ),
                path=path,
                node_id=node.id,
                suggestion="Pick one of the allowed values from the dropdown.",
            )

    if described.type in ("number", "integer"):
        for value in candidates:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                issue(
                    "RULE_TYPE_MISMATCH",
                    (
                        f"{label}: {condition.field} is a {described.type} but "
                        f"is compared with {value!r}."
                    ),
                    path=path,
                    node_id=node.id,
                    suggestion="Enter a number.",
                )
                continue
            _check_threshold(
                condition, described, value, node, path, issue, label
            )


def _check_threshold(
    condition: Condition,
    described: FieldPath,
    value: float,
    node: NodeSpec,
    path: str,
    issue: Callable[..., None],
    label: str,
) -> None:
    """Catch a threshold that can never do anything.

    A confidence gate written as `confidence >= 80` (meaning 80%) against a
    field declared 0–1 is the single most common version of this: it silently
    never fires, so every request looks confident enough and nothing is ever
    escalated. That is the kind of bug a demo does not survive.
    """
    bounds = _numeric_bounds(described)
    if bounds is None:
        return
    minimum, maximum = bounds
    if minimum is not None and value < minimum:
        issue(
            "INVALID_THRESHOLD",
            (
                f"{label}: {condition.field} is never below {minimum}, so the "
                f"threshold {value} is outside its range."
            ),
            path=path,
            node_id=node.id,
            suggestion=f"Use a value between {minimum} and {maximum}.",
        )
    elif maximum is not None and value > maximum:
        issue(
            "INVALID_THRESHOLD",
            (
                f"{label}: {condition.field} is never above {maximum}, so the "
                f"threshold {value} can never be met. A confidence declared "
                f"{minimum}–{maximum} is a fraction, not a percentage."
            ),
            path=path,
            node_id=node.id,
            suggestion=f"Use a value between {minimum} and {maximum}.",
        )


def _numeric_bounds(described: FieldPath) -> tuple[float | None, float | None] | None:
    """Bounds for a numeric field, when the schema declares them.

    Only `confidence` gets an implicit 0–1 range: it is generated by the AI Task
    itself with that constraint, so the bound is a fact about the platform rather
    than a guess about the author's data.
    """
    if described.path.endswith("confidence") or described.path == "confidence":
        return (0.0, 1.0)
    return None


def _resolve_reference(
    reference: str,
    *,
    node: NodeSpec,
    index: FieldIndex,
    nodes: dict[str, NodeSpec],
    path: str,
    issue: Callable[..., None],
    guaranteed_before: Callable[[str, str], bool] | None,
    label: str,
) -> FieldPath | None:
    """Resolve a rule/route reference, reporting what is wrong with it.

    Returns the typed description when the reference is provably valid, and None
    when it is invalid *or* unprovable — callers treat both as "no further type
    checking possible", which keeps every downstream check from having to
    re-decide whether silence was warranted.
    """
    if reference.startswith(("inputs.", "variables.", "decisions.")):
        # `decisions.` addresses values this same node's earlier rules set, and
        # inputs/variables are validated by preflight's existing input checks.
        return None

    source_node = index.node_of(reference)
    if source_node is None:
        issue(
            "UNKNOWN_FIELD_REFERENCE",
            (
                f"{label}: {reference} does not name a workflow value. Use "
                "outputs.<step>.<field>, inputs.<name>, or variables.<name>."
            ),
            path=path,
            node_id=node.id,
            suggestion="Pick the value from the field picker instead of typing it.",
        )
        return None

    if source_node not in nodes:
        issue(
            "UNKNOWN_FIELD_REFERENCE",
            f"{label}: {reference} refers to step {source_node!r}, which does "
            "not exist in this workflow.",
            path=path,
            node_id=node.id,
        )
        return None

    if guaranteed_before is not None and not guaranteed_before(source_node, node.id):
        issue(
            "AI_OUTPUT_NOT_AVAILABLE_UPSTREAM",
            (
                f"{label}: {reference} comes from {source_node!r}, which does "
                f"not always run before {node.id!r}. On the paths where it "
                "doesn't, this condition sees nothing."
            ),
            path=path,
            node_id=node.id,
            suggestion=(
                f"Connect {source_node!r} upstream of {node.id!r}, or move the "
                "condition to a step that always follows it."
            ),
        )
        return None

    described = index.get(reference)
    if described is None:
        if not index.typed_nodes.get(source_node, False):
            # The source's output is a free-form dict — the reference may be
            # perfectly valid at runtime and nothing here can prove otherwise.
            return None
        # A plain alphabetical slice of the available paths is close to useless:
        # it truncates before reaching the field the author meant. Lead with the
        # nearest names — a mistyped reference is almost always one edit away
        # from a real one — and only then show a sample of the rest.
        available = sorted(item.path for item in index.paths_for(source_node))
        wanted = reference.split(".", 2)[-1]
        nearest = get_close_matches(wanted, available, n=3, cutoff=0.6)
        detail = f"Did you mean {nearest}? " if nearest else ""
        sample = [item for item in available if item not in nearest][:12]
        issue(
            "UNKNOWN_FIELD_REFERENCE",
            (
                f"{label}: {source_node!r} does not produce {reference}. "
                f"{detail}It produces: {[*nearest, *sample]}"
                + (f" and {len(available) - len(nearest) - len(sample)} more." if len(available) > len(nearest) + len(sample) else ".")
            ),
            path=path,
            node_id=node.id,
            suggestion=(
                f"Correct the field name{' to ' + nearest[0] if nearest else ''}, "
                "or add it to the upstream step's output schema."
            ),
        )
        return None

    if described.may_be_unavailable and described.required:
        issue(
            "REQUIRED_FIELD_MAY_BE_NULL",
            (
                f"{label}: {reference} is optional or nullable upstream, so it "
                "can be null at runtime. The condition will simply not match "
                "when it is."
            ),
            severity="warning",
            path=path,
            node_id=node.id,
            suggestion=(
                "Add an `exists` condition first, or make the upstream field "
                "required and non-nullable."
            ),
        )

    return described


def _check_ai_task(
    node: NodeSpec,
    config: dict[str, Any],
    issue: Callable[..., None],
) -> None:
    """Contract sanity on a visually built schema."""
    from app.nodes.ai_task import effective_fields

    try:
        fields = effective_fields(config)
    except (ValidationError, ValueError):
        return

    for path in field_paths(fields):
        if path.type == "enum" and not path.enum_values:
            issue(
                "INVALID_ENUM_VALUE",
                f"Output field {path.path!r} is an enum with no allowed values.",
                path=f"nodes.{node.id}.config.output_fields",
                node_id=node.id,
                suggestion="Add the allowed values, or change the field type.",
            )
        if path.required and path.nullable and path.type == "enum":
            issue(
                "REQUIRED_FIELD_MAY_BE_NULL",
                (
                    f"Output field {path.path!r} is required but nullable, so "
                    "downstream rules must handle null explicitly."
                ),
                severity="warning",
                path=f"nodes.{node.id}.config.output_fields",
                node_id=node.id,
                suggestion=(
                    "That is often correct for extraction — add a catch-all "
                    "enum value if you would rather never see null."
                ),
            )


def _check_external_actions(
    spec: WorkflowSpec,
    issue: Callable[..., None],
    guaranteed_before: Callable[[str, str], bool] | None,
) -> None:
    """An outward-facing action with nothing between it and the model.

    A warning, not an error: an author may legitimately decide that an automated
    reply is acceptable (§48 is about making the decision explicit, not about
    forbidding one of the answers). What must not happen is that the decision is
    made silently.
    """
    from app.nodes.email_integration import is_side_effect

    human_gates = {
        node.id for node in spec.nodes if node.type == "HumanInLoopAgent"
    }

    def reviewed_before(node_id: str) -> bool:
        if not human_gates:
            return False
        if guaranteed_before is None:
            return True
        return any(guaranteed_before(gate, node_id) for gate in human_gates)

    for node in spec.nodes:
        if node.type == "EmailAgent":
            operation = node.effective_config().get("operation", "search")
            if not is_side_effect(operation) or operation == "create_draft":
                # A draft is the safe form of an outward action: nothing reaches
                # the recipient until a person sends it.
                continue
            if not reviewed_before(node.id):
                issue(
                    "EXTERNAL_ACTION_WITHOUT_REVIEW",
                    (
                        f"Step {node.id!r} performs an email {operation} with no "
                        "human review guaranteed before it. Model-generated "
                        "content would reach the recipient unchecked."
                    ),
                    severity="warning",
                    path=f"nodes.{node.id}.config.operation",
                    node_id=node.id,
                    suggestion=(
                        "Add a Human Review step upstream, or switch the "
                        "operation to Create Draft so a person sends it."
                    ),
                )
            continue

        if node.type == "MCPToolAgent":
            _check_mcp_tool(node, issue, reviewed_before)


def _check_mcp_tool(
    node: NodeSpec,
    issue: Callable[..., None],
    reviewed_before: Callable[[str], bool],
) -> None:
    """Validate an MCP tool step without contacting the server.

    Preflight is zero-token *and* zero-network — a Builder check must not depend
    on a CRM being reachable. So this checks what is decidable offline: that the
    server is configured, that the tool is permitted by the connection's
    allowlist, and that a write is not scheduled to run unattended. Whether the
    tool actually exists is checked by the Builder's discovery panel, which can
    reach the server.
    """
    from app.mcp.connections import build_registry
    from app.mcp.policy import classify_tool, is_write

    config = node.effective_config()
    server_id = str(config.get("server_id") or "")
    tool_name = str(config.get("tool") or "")

    if not server_id or not tool_name:
        issue(
            "MCP_TOOL_NOT_CONFIGURED",
            f"Step {node.id!r} has no MCP server or tool selected.",
            path=f"nodes.{node.id}.config",
            node_id=node.id,
            suggestion="Pick a server, then a tool, in the Configure tab.",
        )
        return

    try:
        registry = build_registry()
    except Exception:
        # Registry construction reads deployment configuration; if that is
        # broken, other checks report it far better than this one could.
        return

    connection = registry.get(server_id)
    if connection is None:
        issue(
            "MCP_SERVER_NOT_CONFIGURED",
            (
                f"Step {node.id!r} uses MCP server {server_id!r}, which is not "
                f"configured in this deployment. Available: "
                f"{sorted(registry.ids) or 'none'}."
            ),
            path=f"nodes.{node.id}.config.server_id",
            node_id=node.id,
            suggestion=(
                "Pick a configured connection, or add the server to the "
                "deployment configuration."
            ),
        )
        return

    if not connection.permits_tool(tool_name):
        issue(
            "MCP_TOOL_NOT_ALLOWED",
            (
                f"Tool {tool_name!r} is not permitted on connection "
                f"{connection.label!r}."
            ),
            path=f"nodes.{node.id}.config.tool",
            node_id=node.id,
            suggestion=(
                "Choose a permitted tool, or widen the connection's allowlist "
                "in the deployment configuration."
            ),
        )
        return

    operation = classify_tool(tool_name, connection=connection)
    if not is_write(operation):
        return

    if connection.write_policy == "read_only":
        issue(
            "MCP_WRITE_NOT_PERMITTED",
            (
                f"Step {node.id!r} calls {tool_name!r}, which changes data, but "
                f"{connection.label!r} is configured read-only. This step would "
                "fail at run time."
            ),
            path=f"nodes.{node.id}.config.tool",
            node_id=node.id,
            suggestion="Use a read tool, or a connection permitted to write.",
        )
        return

    declared_unattended = bool(config.get("allow_unattended_write"))
    if reviewed_before(node.id) or declared_unattended:
        # An explicit unattended write is a decision the author made and can be
        # seen on the canvas. That is the requirement — not that it be forbidden.
        return

    issue(
        "EXTERNAL_ACTION_WITHOUT_REVIEW",
        (
            f"Step {node.id!r} calls {tool_name!r}, which changes data in "
            f"{connection.label!r}, with no human review guaranteed before it. "
            "It would be refused at run time."
        ),
        severity="warning",
        path=f"nodes.{node.id}.config.tool",
        node_id=node.id,
        suggestion=(
            "Add a Human Review step upstream, or tick 'allow unattended "
            "write' to state that this may happen without one."
        ),
    )


def unreachable_branch_targets(
    spec: WorkflowSpec, reachable: Iterable[str]
) -> list[str]:
    """Router branch targets no run can reach. Complements the graph
    reachability check by naming the *branch*, which is what an author sees."""
    reachable_set = set(reachable)
    return sorted(
        {
            target
            for edge in spec.edges
            for target in (edge.branches or {}).values()
            if target not in reachable_set
        }
    )
