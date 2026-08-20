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
from app.runtime.field_schema import FieldPath, FieldSpec, field_paths, parse_fields
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

    def transform_agent_paths(config: dict[str, Any]) -> list[FieldPath]:
        from app.nodes.transform import TransformConfig, is_new_style

        parsed = TransformConfig(**config)
        found = [FieldPath(path="raw", type="string", description="The model's unparsed response.")]

        if is_new_style(parsed):
            if not parsed.output_fields:
                # Matches TransformAgent._run_new_style: with no output_fields
                # the model is asked for free text and "parsed" is hardcoded
                # to {} forever — a reference into it is a dead end.
                return found
            found.append(FieldPath(path="parsed", type="object", description="The extracted fields."))
            found.extend(
                path.model_copy(update={"path": f"parsed.{path.path}"})
                for path in field_paths(parsed.output_fields)
            )
            return found

        declared = parsed.output_schema
        if not declared:
            # Matches TransformAgent._run_legacy: with no output_schema the
            # model is asked for free text and "parsed" is hardcoded to {}
            # forever — a reference into it is a dead end, so only "raw" is
            # offered.
            return found
        found.append(FieldPath(path="parsed", type="object", description="The extracted fields."))
        for key, type_str in declared.items():
            found.append(
                FieldPath(
                    path=f"parsed.{key}",
                    type=_TRANSFORM_AGENT_KINDS.get(type_str.lower(), "unknown"),
                )
            )
        return found

    def mcp_tool_paths(config: dict[str, Any]) -> list[FieldPath]:
        schema = _static_mcp_output_schema(config.get("server_id"), config.get("tool"))
        if not schema:
            # Unknown tool — a live/third-party server, a renamed/retired
            # mock tool, or one this platform doesn't ship a static schema
            # for. Nothing more to add; build_field_index never treats a
            # MCPToolAgent node as closed-world regardless (see its
            # MCPToolAgent special case), so an empty list here is exactly
            # as permissive as before this builder existed.
            return []

        found = _json_schema_paths(schema, "data")
        properties = schema.get("properties") or {}
        # Mirrors MCPToolAgent._summarise's own runtime heuristic exactly: the
        # first array-of-objects property becomes `first.*`; failing that, a
        # single object-valued property does. Keeping these in lockstep is
        # what makes `first.account_id` a real, checkable path rather than a
        # guess at what the node will actually put there.
        collection_key = next(
            (
                name for name, sub in properties.items()
                if isinstance(sub, dict)
                and _json_schema_kind(sub) == "list"
                and isinstance(sub.get("items"), dict)
                and _json_schema_kind(sub["items"]) == "object"
            ),
            None,
        )
        if collection_key:
            found.extend(_json_schema_paths(properties[collection_key]["items"], "first"))
        else:
            object_keys = [
                name for name, sub in properties.items()
                if isinstance(sub, dict) and _json_schema_kind(sub) == "object"
            ]
            if len(object_keys) == 1:
                found.extend(_json_schema_paths(properties[object_keys[0]], "first"))
        return found

    def subprocess_output_paths(config: dict[str, Any]) -> list[FieldPath]:
        child_spec = _static_subprocess_child_spec(config)
        if child_spec is None or child_spec.output is None:
            # No declared output: contract on the child, or the child
            # couldn't be resolved statically (missing/invalid — the
            # dedicated subprocess check reports that). Either way, `result`
            # stays a generic untyped object rather than a guess.
            return []

        found: list[FieldPath] = []
        for output_node in child_spec.output.nodes:
            node_paths = _shallow_child_node_paths(child_spec, output_node.node_id)
            prefix = (
                "result" if output_node.flatten
                else f"result.{output_node.node_id}"
            )
            found.extend(
                path.model_copy(update={"path": f"{prefix}.{path.path}"})
                for path in node_paths
            )
        return found

    def python_snippet_paths(config: dict[str, Any]) -> list[FieldPath]:
        from app.nodes.python_snippet import PythonSnippetConfig

        parsed = PythonSnippetConfig(**config)
        if not parsed.output_fields:
            # No declared shape: `result` stays a generic object rather
            # than a guess — same convention as TransformAgent's own
            # no-schema case.
            return []
        return [
            path.model_copy(update={"path": f"result.{path.path}"})
            for path in field_paths(parsed.output_fields)
        ]

    _TYPED_OUTPUT_BUILDERS.update(
        {
            "AITaskAgent": ai_task_paths,
            "WorkflowInputAgent": input_paths,
            "DecisionAgent": decision_paths,
            "DataTransformAgent": transform_paths,
            "TransformAgent": transform_agent_paths,
            "MCPToolAgent": mcp_tool_paths,
            "SubprocessAgent": subprocess_output_paths,
            "PythonSnippetAgent": python_snippet_paths,
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


#: JSON-Schema `type` -> this platform's own FieldKind vocabulary.
_JSON_SCHEMA_KINDS = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "list",
}


def _json_schema_kind(schema: dict[str, Any]) -> str:
    type_ = schema.get("type")
    if isinstance(type_, list):
        # e.g. ["string", "null"] from an Optional field — the null arm carries
        # no shape information, so the first real type wins.
        type_ = next((item for item in type_ if item != "null"), None)
    return _JSON_SCHEMA_KINDS.get(type_, "unknown")


def _json_schema_paths(schema: dict[str, Any], prefix: str) -> list[FieldPath]:
    """Flatten a JSON-Schema object's `properties` into typed dotted paths.

    Mirrors `_field_paths`' own `<list>.items.<name>` convention for a list of
    objects (app/runtime/field_schema.py), so a tool's declared output_schema
    reads through the exact same picker/rule-editor machinery as a visually
    authored schema — an MCP tool's `data.accounts.items.account_id` is not a
    special case, it is the same mechanism DataTransformAgent's item fields use.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = {name for name in (schema.get("required") or []) if isinstance(name, str)}

    found: list[FieldPath] = []
    for name, subschema in properties.items():
        if not isinstance(subschema, dict):
            continue
        path = f"{prefix}.{name}"
        kind = _json_schema_kind(subschema)
        items_schema = subschema.get("items") if kind == "list" else None
        item_kind = (
            _json_schema_kind(items_schema)
            if isinstance(items_schema, dict)
            else None
        )
        found.append(
            FieldPath(
                path=path,
                type=kind,
                description=str(subschema.get("description") or ""),
                required=name in required,
                item_type=item_kind if item_kind and item_kind != "unknown" else None,
                may_be_unavailable=name not in required,
            )
        )
        if kind == "object":
            found.extend(_json_schema_paths(subschema, path))
        elif kind == "list" and isinstance(items_schema, dict) and item_kind == "object":
            found.extend(_json_schema_paths(items_schema, f"{path}.items"))
    return found


def _static_mcp_tool_registries() -> dict[str, dict[str, dict[str, Any]]]:
    """Per-server `TOOLS_BY_NAME` registries for the built-in MCP connectors.

    Only the built-in servers whose tool vocabulary is a plain Python object
    (loaded at import time, no live round trip) can be resolved this way —
    the F&O connector's `live` mode and any third-party MCP_SERVERS entry are
    genuinely unknown until the server itself is asked, which is exactly what
    the Builder's tool-discovery panel is for. A tool name is unambiguous
    either way: `dynamics365_finance_scm`'s mock and live modes never share a
    tool name, so resolving it here can never describe the wrong backend.
    """
    from app.mcp.business_records.tools import TOOLS_BY_NAME as _BUSINESS_RECORDS_TOOLS
    from app.mcp.d365_finance.tools import TOOLS_BY_NAME as _D365_FINANCE_TOOLS
    from app.mcp.dynamics.tools import TOOLS_BY_NAME as _DYNAMICS_TOOLS

    return {
        "business_records": _BUSINESS_RECORDS_TOOLS,
        "dynamics365_finance_scm": _D365_FINANCE_TOOLS,
        "dynamics365": _DYNAMICS_TOOLS,
        "dynamics365_readonly": _DYNAMICS_TOOLS,
    }


def _static_mcp_tool_definition(server_id: Any, tool_name: Any) -> dict[str, Any] | None:
    if not isinstance(server_id, str) or not isinstance(tool_name, str):
        return None
    return _static_mcp_tool_registries().get(server_id, {}).get(tool_name)


def _static_mcp_output_schema(server_id: Any, tool_name: Any) -> dict[str, Any] | None:
    """The declared output_schema for a tool this platform ships itself."""
    definition = _static_mcp_tool_definition(server_id, tool_name)
    if not definition:
        return None
    schema = definition.get("output_schema")
    return schema if isinstance(schema, dict) else None


def _static_mcp_required_arguments(
    server_id: Any, tool_name: Any, arguments: dict[str, Any]
) -> list[str] | None:
    """Argument names the tool's own static schema requires but `arguments`
    never mentions — or `None` when this tool isn't one preflight can see
    statically (a live/third-party server), in which case there is nothing to
    report. Mirrors `app.nodes.mcp_tool._required_arguments`'s `anyOf` handling
    exactly, but offline: this only checks that *a* key was authored for each
    required name, never whether it resolves to a real value at run time —
    that half of the question still belongs to the run itself.
    """
    definition = _static_mcp_tool_definition(server_id, tool_name)
    if not definition:
        return None
    schema = definition.get("input_schema")
    if not isinstance(schema, dict):
        return None

    required = schema.get("required")
    names = {name for name in required if isinstance(name, str)} if isinstance(required, list) else set()

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        provided = {name for name, value in arguments.items() if value not in (None, "")}
        alternatives = [
            set(alt["required"])
            for alt in any_of
            if isinstance(alt, dict) and isinstance(alt.get("required"), list)
        ]
        if alternatives and not any(alt <= provided for alt in alternatives):
            names |= {name for alt in alternatives for name in alt}

    missing = sorted(name for name in names if name not in arguments)
    return missing


def _static_subprocess_child_spec(config: dict[str, Any]) -> WorkflowSpec | None:
    """Load the referenced workflow's spec purely to read its shape (declared
    output contract) — None for anything not statically resolvable (missing
    name, bad charset, file not found, fails to parse). The dedicated
    subprocess check (_check_subprocess_agents) is what reports those as
    authoring errors; this just declines to guess a shape it doesn't have.
    """
    import re
    from pathlib import Path

    name = config.get("workflow")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return None
    path = Path("workflows") / f"{name}.yaml"
    if not path.exists():
        return None
    try:
        from app.runtime.loader import load_workflow_from_string

        return load_workflow_from_string(path.read_text())
    except Exception:
        return None


def _shallow_child_node_paths(child_spec: WorkflowSpec, node_id: str) -> list[FieldPath]:
    """One node's typed output paths, one level deep, deliberately NOT a
    recursive call into build_field_index(child_spec): a SubprocessAgent
    output node's own further-nested child is left untyped rather than
    resolved, so a long — or cyclic — subprocess reference chain can never
    blow the Python call stack just to compute field paths. (A genuine cycle
    is instead reported cleanly as SUBPROCESS_RECURSION, by the iterative,
    non-recursive walker in _check_subprocess_agents.)
    """
    child_node = next((n for n in child_spec.nodes if n.id == node_id), None)
    if child_node is None or child_node.type == "SubprocessAgent":
        return []

    _register_builders()
    found: list[FieldPath] = []
    try:
        found.extend(_declared_output_paths(NodeRegistry.get(child_node.type)))
    except Exception:
        pass
    builder = _TYPED_OUTPUT_BUILDERS.get(child_node.type)
    if builder is not None:
        try:
            found.extend(builder(child_node.effective_config()))
        except (ValidationError, ValueError):
            pass
    return found


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


#: Mirrors TransformAgent's own `_TYPE_MAP` (app/nodes/transform.py) — the
#: author writes "str"/"int"/"float"/... in `output_schema`, so the contract
#: needs the same aliases to report a type instead of "unknown".
_TRANSFORM_AGENT_KINDS = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "list": "list",
    "dict": "object",
    "object": "object",
}


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

        if node.type == "PythonSnippetAgent" and not node.effective_config().get("output_fields"):
            # No declared output shape: a snippet with no output_fields can
            # still write any key into `output` at runtime, so this node is
            # exactly as closed-world as an MCP tool with an under-specified
            # schema — best-effort only (there is nothing to enrich the
            # index with here, since there is no declared shape at all),
            # never closed-world.
            index.typed_nodes[node.id] = False
            try:
                for path in _declared_output_paths(NodeRegistry.get(node.type)):
                    index.add(node.id, path)
            except Exception:
                pass
            continue

        if node.type in ("MCPToolAgent", "SubprocessAgent"):
            # Best-effort enrichment only, never closed-world. MCPToolAgent:
            # several tools this platform ships declare an under-specified
            # sub-schema (e.g. find_order_fulfilment_status's `fulfilment`
            # is a bare `{"type": "object"}` — the real handler's shape
            # isn't fully mirrored in the static schema). SubprocessAgent:
            # the referenced workflow might not exist yet at authoring time,
            # might have no declared output: contract, or (result_from ==
            # "node"/"all_outputs") is deliberately never typed at all.
            # Either way, marking the node "fully typed" would make the
            # rule/condition checker below reject a reference into a field
            # that is real at runtime but merely undeclared — exactly the
            # false positive a free-form dict is supposed to avoid. So these
            # paths feed the field index (for the Builder's picker, which
            # doesn't gate on `typed_nodes`) while `typed_nodes` stays
            # False, preserving this node type's always-permissive behavior
            # for the strict checks.
            index.typed_nodes[node.id] = False
            try:
                for path in _declared_output_paths(NodeRegistry.get(node.type)):
                    index.add(node.id, path)
            except Exception:
                pass
            try:
                for path in builder(node.effective_config()):
                    index.add(node.id, path)
            except (ValidationError, ValueError):
                pass
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
        elif node.type == "TransformAgent":
            _check_transform_agent(node, config, issue)

    _check_conditional_join_reachability(spec, issue)
    _check_external_actions(spec, issue, guaranteed_before)
    _check_subprocess_agents(spec, issue, guaranteed_before)
    _check_sql_query_agents(spec, issue)
    _check_python_snippet_agents(spec, issue)


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
        # Multi-Route field mode maps EACH element of a list through
        # `branches` (see RouterAgent._route_by_field_multi) — a scalar
        # field can hold at most one value, defeating the entire point of
        # selecting several branches at once.
        if (
            parsed.selection == "multi"
            and described is not None
            and described.type not in ("list", "unknown")
        ):
            issue(
                "MULTIROUTE_FIELD_NOT_A_LIST",
                (
                    f"Multi-Route field mode reads {parsed.route_field}, but "
                    f"it is a {described.type}, not a list — only one value "
                    "can ever be selected, the same as single selection."
                ),
                path=f"nodes.{node.id}.config.route_field",
                node_id=node.id,
                suggestion=(
                    "Point route_field at a list-typed value (e.g. a "
                    "Transform field declared as a list), or switch this "
                    "router back to single selection."
                ),
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

    _check_output_field_enums(node, fields, issue)


def _check_transform_agent(
    node: NodeSpec,
    config: dict[str, Any],
    issue: Callable[..., None],
) -> None:
    """Same contract sanity as `_check_ai_task`, for a new-style TransformAgent
    node's `output_fields`. Legacy nodes (`output_schema`'s plain type map, no
    enum concept) have nothing for this check to do."""
    from app.nodes.transform import is_new_style, TransformConfig

    try:
        if not is_new_style(TransformConfig(**config)):
            return
    except (ValidationError, ValueError):
        return

    try:
        fields = parse_fields(config.get("output_fields") or [])
    except (ValidationError, ValueError):
        return

    _check_output_field_enums(node, fields, issue)


def _check_output_field_enums(
    node: NodeSpec,
    fields: list[FieldSpec],
    issue: Callable[..., None],
) -> None:
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
            config = node.effective_config()
            operation = config.get("operation", "search")
            if not is_side_effect(operation) or operation == "create_draft":
                # A draft is the safe form of an outward action: nothing reaches
                # the recipient until a person sends it.
                continue
            if config.get("allow_unattended_write"):
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
            continue

        if node.type == "MCPAgent":
            _check_mcp_agent(node, issue, reviewed_before)
            continue

        if node.type == "ExternalActionAgent":
            _check_external_action(node, issue, reviewed_before)


def _check_external_action(
    node: NodeSpec,
    issue: Callable[..., None],
    reviewed_before: Callable[[str], bool],
) -> None:
    """A write or external_action call with nothing between it and the model.

    Same shape as MCP's and Email's own checks: the classification is
    authored on the node (§48 — surfaced, not buried), so preflight only
    needs to read it and check whether a human review is guaranteed first.
    """
    config = node.effective_config()
    safety_class = config.get("safety_class")
    if safety_class not in ("write", "external_action"):
        return
    if config.get("allow_unattended_write"):
        return
    if reviewed_before(node.id):
        return
    issue(
        "EXTERNAL_ACTION_WITHOUT_REVIEW",
        (
            f"Step {node.id!r} is a {safety_class} external action with no "
            "human review guaranteed before it and allow_unattended_write is "
            "not set."
        ),
        severity="warning",
        path=f"nodes.{node.id}.config.safety_class",
        node_id=node.id,
        suggestion=(
            "Add a Human Review step upstream, or set allow_unattended_write "
            "if this call is meant to run unattended."
        ),
    )


def _check_subprocess_agents(
    spec: WorkflowSpec,
    issue: Callable[..., None],
    guaranteed_before: Callable[[str, str], bool] | None,
) -> None:
    """Everything about a Subprocess step preflight can prove without
    actually running the child: the referenced workflow exists and parses,
    every explicit input mapping targets a real declared input of it, every
    *required* input of it resolves to something, and the whole reference
    graph this workflow reaches (through its own and every downstream
    workflow's Subprocess steps) is acyclic.
    """
    import re
    from pathlib import Path

    from app.runtime.loader import load_workflow_from_string

    nodes_by_id = {node.id: node for node in spec.nodes}
    declared_inputs = set(spec.inputs)
    name_re = re.compile(r"^[A-Za-z0-9_-]+$")

    root_refs: set[str] = set()

    for node in spec.nodes:
        if node.type != "SubprocessAgent":
            continue
        config = node.effective_config()
        name = str(config.get("workflow") or "").strip()
        if not name or not name_re.fullmatch(name):
            issue(
                "SUBPROCESS_WORKFLOW_NOT_CONFIGURED",
                f"Step {node.id!r} has no valid workflow selected.",
                path=f"nodes.{node.id}.config.workflow",
                node_id=node.id,
                suggestion="Pick a saved workflow to run as a subprocess.",
            )
            continue

        root_refs.add(name)
        child_path = Path("workflows") / f"{name}.yaml"
        if not child_path.exists():
            issue(
                "SUBPROCESS_WORKFLOW_NOT_FOUND",
                (
                    f"Step {node.id!r} references workflow {name!r}, which "
                    "does not exist."
                ),
                path=f"nodes.{node.id}.config.workflow",
                node_id=node.id,
                suggestion="Pick an existing saved workflow.",
            )
            continue

        try:
            child_spec = load_workflow_from_string(child_path.read_text())
        except Exception as exc:
            issue(
                "SUBPROCESS_WORKFLOW_INVALID",
                (
                    f"Step {node.id!r} references workflow {name!r}, which "
                    f"failed to load: {exc}."
                ),
                path=f"nodes.{node.id}.config.workflow",
                node_id=node.id,
            )
            continue

        explicit_inputs = config.get("inputs")
        explicit_inputs = explicit_inputs if isinstance(explicit_inputs, dict) else {}

        for input_name in explicit_inputs:
            if input_name in child_spec.inputs:
                continue
            issue(
                "SUBPROCESS_INPUT_NOT_DECLARED",
                (
                    f"Step {node.id!r} maps {input_name!r}, which {name!r} "
                    "does not declare as an input — this value is dropped, "
                    "not delivered."
                ),
                path=f"nodes.{node.id}.config.inputs.{input_name}",
                node_id=node.id,
                suggestion=f"Remove it, or check {name!r}'s declared inputs.",
            )

        for input_name, input_spec in child_spec.inputs.items():
            if not input_spec.required or input_name in explicit_inputs:
                continue
            if input_name in declared_inputs:
                continue
            source_node = nodes_by_id.get(input_name)
            if source_node is not None and (
                guaranteed_before is None
                or guaranteed_before(input_name, node.id)
            ):
                continue
            issue(
                "SUBPROCESS_REQUIRED_INPUT_UNRESOLVED",
                (
                    f"Step {node.id!r} calls {name!r}, which requires "
                    f"{input_name!r} — nothing maps to it."
                ),
                path=f"nodes.{node.id}.config.inputs",
                node_id=node.id,
                suggestion=(
                    f"Map {input_name!r} explicitly, or name a workflow "
                    f"input or an upstream step {input_name!r}."
                ),
            )

    cycle = _find_subprocess_cycle(root_refs)
    if cycle:
        issue(
            "SUBPROCESS_RECURSION",
            (
                "Subprocess workflows reference each other in a cycle: "
                f"{' -> '.join(cycle)}."
            ),
            suggestion="Break the cycle — a subprocess chain must terminate.",
        )


def _load_subprocess_refs(name: str) -> set[str]:
    """Workflow names a *saved* workflow's own SubprocessAgent nodes
    reference. Empty for a missing or unparsable file — those are reported
    as authoring errors by _check_subprocess_agents itself, not here."""
    from pathlib import Path

    from app.runtime.loader import load_workflow_from_string

    path = Path("workflows") / f"{name}.yaml"
    if not path.exists():
        return set()
    try:
        spec = load_workflow_from_string(path.read_text())
    except Exception:
        return set()
    return {
        str(node.effective_config().get("workflow") or "").strip()
        for node in spec.nodes
        if node.type == "SubprocessAgent" and node.effective_config().get("workflow")
    }


def _find_subprocess_cycle(entry_refs: set[str]) -> list[str] | None:
    """Iterative cycle detection over the on-disk subprocess call graph.

    One iterative, memoised worklist walker — never mutually-recursive
    callbacks, which is exactly what caused a real RecursionError in an
    earlier design for this feature. Each on-disk workflow file is parsed at
    most once (`memo`). Returns the cyclic path if one exists, else None.

    Standard two-colour (gray/black) iterative DFS: `color[name] == 1` means
    "on the current path" (a return to it is a real cycle), `== 2` means
    "fully explored, clean". The explicit stack of `[name, children, index]`
    frames is what replaces Python call recursion.
    """
    memo: dict[str, set[str]] = {}

    def refs_of(name: str) -> list[str]:
        cached = memo.get(name)
        if cached is None:
            cached = _load_subprocess_refs(name)
            memo[name] = cached
        return sorted(cached)

    color: dict[str, int] = {}
    for start in sorted(entry_refs):
        if color.get(start) == 2:
            continue
        stack: list[list[Any]] = [[start, refs_of(start), 0]]
        color[start] = 1
        while stack:
            frame = stack[-1]
            frame_name, children, i = frame
            if i >= len(children):
                color[frame_name] = 2
                stack.pop()
                continue
            frame[2] += 1
            nxt = children[i]
            state = color.get(nxt)
            if state == 1:
                return [f[0] for f in stack] + [nxt]
            if state != 2:
                color[nxt] = 1
                stack.append([nxt, refs_of(nxt), 0])
    return None


def _check_sql_query_agents(spec: WorkflowSpec, issue: Callable[..., None]) -> None:
    """A literal `{{...}}` inside the `sql` field text would be substituted
    by the compiler's generic templating pass before run() ever sees it —
    landing a mapped value directly in SQL text instead of going through
    `params` as a bound `%(name)s` placeholder. That is exactly the
    SQL-injection shape this node type exists to prevent (see
    app/nodes/sql_query.py's own module docstring), so it is an authoring
    error, not a style preference.
    """
    for node in spec.nodes:
        if node.type != "SQLQueryAgent":
            continue
        sql = node.effective_config().get("sql")
        if not isinstance(sql, str):
            continue
        if "{{" in sql:
            issue(
                "SQL_QUERY_TEMPLATE_IN_TEXT",
                (
                    f"Step {node.id!r}'s sql field contains a {{{{...}}}} "
                    "reference. A mapped value belongs in params, referenced "
                    "in the SQL text as %(name)s — never substituted "
                    "directly into the query string."
                ),
                path=f"nodes.{node.id}.config.sql",
                node_id=node.id,
                suggestion="Move the mapped value into params and reference it as %(name)s.",
            )
        if "%s" in sql:
            issue(
                "SQL_QUERY_POSITIONAL_PARAM",
                (
                    f"Step {node.id!r}'s sql field uses a bare %s "
                    "placeholder — query_readonly binds params by name, so "
                    "this will fail at run time."
                ),
                severity="warning",
                path=f"nodes.{node.id}.config.sql",
                node_id=node.id,
                suggestion="Use a %(name)s placeholder matching a key in params.",
            )


def _check_python_snippet_agents(spec: WorkflowSpec, issue: Callable[..., None]) -> None:
    """Everything decidable about a Python snippet without running it:
    a syntax error (would fail every run identically, so it is an error, not
    a warning), plus authoring hints — an import with no effect inside the
    sandbox, a dunder-attribute access, or a snippet that never touches
    `output` at all. None of these are the actual security boundary (that is
    the sidecar's own network_mode: none and resource limits, which hold
    regardless of what a snippet imports) — they are just help an author
    would otherwise only get by running it and finding out.
    """
    from app.nodes.python_snippet import assigns_output, scan_snippet_for_warnings

    for node in spec.nodes:
        if node.type != "PythonSnippetAgent":
            continue
        code = node.effective_config().get("code")
        if not isinstance(code, str) or not code.strip():
            continue

        try:
            compile(code, "<snippet>", "exec")
        except SyntaxError as exc:
            issue(
                "SNIPPET_SYNTAX_ERROR",
                f"Step {node.id!r}'s code has a syntax error: {exc.msg} (line {exc.lineno}).",
                path=f"nodes.{node.id}.config.code",
                node_id=node.id,
                suggestion="Fix the syntax error before this step can run.",
            )
            continue

        for hint in scan_snippet_for_warnings(code):
            issue(
                "SNIPPET_SUSPICIOUS_CODE",
                f"Step {node.id!r}'s code {hint}.",
                severity="warning",
                path=f"nodes.{node.id}.config.code",
                node_id=node.id,
                suggestion="Remove it if unintentional — it has no effect in the sandbox either way.",
            )

        if not assigns_output(code):
            issue(
                "SNIPPET_NEVER_SETS_OUTPUT",
                f"Step {node.id!r}'s code never appears to write to `output` — its result will be empty.",
                severity="warning",
                path=f"nodes.{node.id}.config.code",
                node_id=node.id,
                suggestion="Assign the fields you want downstream steps to read, e.g. output['field'] = ....",
            )


def _check_mcp_agent(
    node: NodeSpec,
    issue: Callable[..., None],
    reviewed_before: Callable[[str], bool],
) -> None:
    """MCPAgent chooses its own tools at runtime, so — unlike MCPToolAgent —
    there is no single static tool name preflight can classify read/write
    against. What preflight *can* check without a server round-trip: whether
    the author narrowed the tool surface at all, and whether a human reviews
    the outcome before it can have an effect outside the run."""

    config = node.effective_config()
    if config.get("allowed_tools"):
        return
    if reviewed_before(node.id):
        return
    issue(
        "EXTERNAL_ACTION_WITHOUT_REVIEW",
        (
            f"Step {node.id!r} is an autonomous tool-calling agent with no "
            "allowed_tools restriction and no human review guaranteed before "
            "it. It may call any tool the connected server exposes."
        ),
        severity="warning",
        path=f"nodes.{node.id}.config.allowed_tools",
        node_id=node.id,
        suggestion=(
            "Set allowed_tools to the specific tools this step needs, or add "
            "a Human Review step upstream."
        ),
    )


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

    arguments = config.get("arguments")
    missing = _static_mcp_required_arguments(
        server_id, tool_name, arguments if isinstance(arguments, dict) else {}
    )
    if missing:
        issue(
            "MCP_REQUIRED_ARGUMENT_MISSING",
            (
                f"Step {node.id!r} calls {tool_name!r} without a value for "
                f"{', '.join(missing)!r} — {tool_name} requires it."
            ),
            path=f"nodes.{node.id}.config.arguments",
            node_id=node.id,
            suggestion=(
                "Map a value for this argument in the Configure tab, or "
                "leave it if it's genuinely optional in the tool's schema."
            ),
        )

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
