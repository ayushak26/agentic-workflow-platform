"""Zero-token workflow validation.

The preflight validates YAML, registry discovery, node contracts/config,
models, templates, graph topology, compilation, inputs, and required services
without calling an LLM. API run/retry routes use the strict service mode before
creating a run record; the Builder and CLI use structural mode while editing.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from difflib import get_close_matches
from enum import Enum
import inspect
import re
from typing import Any, Iterable, Literal, get_args

from pydantic import BaseModel, Field, ValidationError
import yaml

import app.nodes as nodes_package
from app.config import settings
from app.llm.catalog import local_service_name
from app.llm.model_catalog import AUTO_MODEL
from app.llm.openrouter_catalog import is_openrouter_model_id
from app.llm.registry import resolve_model
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.runtime.schema import (
    DEFAULT_LLM_MODELS,
    EdgeSpec,
    NodeSpec,
    WorkflowSpec,
)
from app.runtime.templating import TEMPLATE_RE


class DuplicateYamlKeyError(ValueError):
    """Raised when YAML silently would have overwritten a duplicate key."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            line = key_node.start_mark.line + 1
            column = key_node.start_mark.column + 1
            raise DuplicateYamlKeyError(
                f"duplicate YAML key {key!r} at line {line}, column {column}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class PreflightSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class PreflightIssue(BaseModel):
    code: str
    severity: PreflightSeverity
    message: str
    path: str | None = None
    node_id: str | None = None
    suggestion: str | None = None


class PreflightCheck(BaseModel):
    name: str
    status: Literal["passed", "failed", "warning", "skipped"]
    detail: str = ""


class WorkflowPreflightReport(BaseModel):
    valid: bool
    workflow_name: str | None = None
    node_count: int = 0
    edge_count: int = 0
    required_services: list[str] = Field(default_factory=list)
    checks: list[PreflightCheck] = Field(default_factory=list)
    issues: list[PreflightIssue] = Field(default_factory=list)
    tokens_spent: int = 0

    @property
    def errors(self) -> list[PreflightIssue]:
        return [
            issue
            for issue in self.issues
            if issue.severity == PreflightSeverity.ERROR
        ]

    @property
    def warnings(self) -> list[PreflightIssue]:
        return [
            issue
            for issue in self.issues
            if issue.severity == PreflightSeverity.WARNING
        ]

    def refresh(self) -> "WorkflowPreflightReport":
        self.valid = not self.errors
        return self


class WorkflowPreflightError(ValueError):
    """Carries the complete report when execution is blocked."""

    def __init__(self, report: WorkflowPreflightReport):
        self.report = report
        summary = "; ".join(issue.message for issue in report.errors[:5])
        super().__init__(
            f"Workflow preflight failed with {len(report.errors)} error(s): "
            f"{summary}"
        )


def _issue(
    report: WorkflowPreflightReport,
    code: str,
    message: str,
    *,
    severity: PreflightSeverity = PreflightSeverity.ERROR,
    path: str | None = None,
    node_id: str | None = None,
    suggestion: str | None = None,
) -> None:
    report.issues.append(
        PreflightIssue(
            code=code,
            severity=severity,
            message=message,
            path=path,
            node_id=node_id,
            suggestion=suggestion,
        )
    )


def _add_check(
    report: WorkflowPreflightReport,
    name: str,
    before: int,
    detail: str,
) -> None:
    new_issues = report.issues[before:]
    if any(
        item.severity == PreflightSeverity.ERROR for item in new_issues
    ):
        status = "failed"
    elif new_issues:
        status = "warning"
    else:
        status = "passed"
    report.checks.append(
        PreflightCheck(name=name, status=status, detail=detail)
    )


def _load_unique_yaml(yaml_text: str) -> Any:
    return yaml.load(yaml_text, Loader=_UniqueKeyLoader)


def _parse_spec(
    yaml_text: str,
    report: WorkflowPreflightReport,
) -> WorkflowSpec | None:
    before = len(report.issues)
    try:
        raw = _load_unique_yaml(yaml_text)
    except DuplicateYamlKeyError as exc:
        _issue(
            report,
            "YAML_DUPLICATE_KEY",
            str(exc),
            suggestion="Remove the duplicate; YAML otherwise keeps only one value.",
        )
        _add_check(report, "yaml", before, "YAML could not be read safely.")
        return None
    except yaml.YAMLError as exc:
        _issue(
            report,
            "YAML_SYNTAX",
            f"Invalid YAML: {exc}",
            suggestion="Fix indentation, quoting, or list/mapping syntax.",
        )
        _add_check(report, "yaml", before, "YAML syntax is invalid.")
        return None

    if not isinstance(raw, dict):
        _issue(
            report,
            "YAML_ROOT",
            "Workflow YAML must contain one top-level mapping/object.",
        )
        _add_check(report, "yaml", before, "YAML root is not a mapping.")
        return None
    _add_check(report, "yaml", before, "YAML parsed with unique keys.")

    before = len(report.issues)
    try:
        spec = WorkflowSpec.model_validate(raw)
    except ValidationError as exc:
        for error in exc.errors(include_url=False):
            location = ".".join(str(part) for part in error["loc"])
            _issue(
                report,
                "WORKFLOW_SCHEMA",
                error["msg"],
                path=location or None,
                suggestion="Correct the workflow field shown in path.",
            )
        _add_check(
            report,
            "workflow_schema",
            before,
            "Workflow contract validation failed.",
        )
        return None

    report.workflow_name = spec.name
    report.node_count = len(spec.nodes)
    report.edge_count = len(spec.edges)
    _add_check(
        report,
        "workflow_schema",
        before,
        "Workflow contract is valid.",
    )
    return spec


def _validate_registry(report: WorkflowPreflightReport) -> None:
    before = len(report.issues)
    discovery_errors = nodes_package.discover_nodes()
    for module, error in sorted(discovery_errors.items()):
        _issue(
            report,
            "NODE_MODULE_IMPORT_FAILED",
            f"Could not import {module}: {error}",
            path=module,
            suggestion="Install the dependency or fix the module import.",
        )

    for type_name, node_class in sorted(NodeRegistry._registry.items()):
        if not inspect.isclass(node_class) or not issubclass(
            node_class,
            NodeType,
        ):
            _issue(
                report,
                "NODE_CONTRACT_INVALID",
                f"Registry entry {type_name!r} is not a NodeType subclass.",
                path=type_name,
            )
            continue
        if node_class.type_name != type_name:
            _issue(
                report,
                "NODE_TYPE_NAME_MISMATCH",
                f"Registry key {type_name!r} does not match "
                f"{node_class.__name__}.type_name={node_class.type_name!r}.",
                path=type_name,
            )
        for attribute in ("input_schema", "config_schema", "output_schema"):
            schema = getattr(node_class, attribute, None)
            if (
                not inspect.isclass(schema)
                or not issubclass(schema, BaseModel)
            ):
                _issue(
                    report,
                    "NODE_SCHEMA_MISSING",
                    f"{type_name} must declare a Pydantic {attribute}.",
                    path=f"{type_name}.{attribute}",
                )
        if not inspect.iscoroutinefunction(node_class.run):
            _issue(
                report,
                "NODE_RUN_NOT_ASYNC",
                f"{type_name}.run must be async.",
                path=f"{type_name}.run",
            )

    _add_check(
        report,
        "node_registry",
        before,
        f"{len(NodeRegistry._registry)} node type(s) discovered.",
    )


def _unknown_config_fields(
    node_spec: NodeSpec,
    node_class: type[NodeType],
) -> list[str]:
    model_config = getattr(node_class.config_schema, "model_config", {})
    if model_config.get("extra") == "allow":
        return []
    known = set(node_class.config_schema.model_fields)
    return sorted(set(node_spec.effective_config()) - known)


def _validated_node_config(node_spec: NodeSpec) -> dict[str, Any]:
    try:
        node_class = NodeRegistry.get(node_spec.type)
        instance = node_class(
            node_spec.id,
            node_spec.effective_config(),
            services={},
        )
        return instance.config.model_dump(mode="python")
    except Exception:
        return node_spec.effective_config()


def _iter_model_values(config: Any, path: str = "config") -> Iterable[tuple[str, str]]:
    if isinstance(config, dict):
        for key, value in config.items():
            child_path = f"{path}.{key}"
            if key in {"model", "generator_model"} and isinstance(value, str):
                yield child_path, value
            elif key in {"models", "evaluator_models"} and isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, str):
                        yield f"{child_path}.{index}", item
            yield from _iter_model_values(value, child_path)
    elif isinstance(config, list):
        for index, value in enumerate(config):
            yield from _iter_model_values(value, f"{path}.{index}")


def _required_services_for_node(node: NodeSpec) -> set[str]:
    """Delegates to the node type's own `required_services()` override (see
    app/nodes/base.py) — every node-type-specific service dependency lives
    beside the node that declares it, not in a central if/elif chain here."""
    config = node.effective_config()
    try:
        node_class = NodeRegistry.get(node.type)
    except KeyError:
        return set()

    required = set(node_class.required_services(config))
    for _, model in _iter_model_values(config):
        service_name = local_service_name(model)
        if service_name:
            required.add(service_name)
    return required


def _validate_nodes(
    spec: WorkflowSpec,
    report: WorkflowPreflightReport,
) -> None:
    before = len(report.issues)
    known_types = sorted(NodeRegistry._registry)
    required_services: set[str] = set()

    for index, node_spec in enumerate(spec.nodes):
        path = f"nodes.{index}"
        required_services.update(_required_services_for_node(node_spec))
        validated_config = node_spec.effective_config()

        for allowed_index, model_name in enumerate(node_spec.allowed_models):
            if model_name not in DEFAULT_LLM_MODELS and not is_openrouter_model_id(
                model_name
            ):
                _issue(
                    report,
                    "MODEL_NOT_IN_CATALOG",
                    f"Allowed model {model_name!r} is not in the approved "
                    "model catalog.",
                    path=f"{path}.allowed_models.{allowed_index}",
                    node_id=node_spec.id,
                    suggestion=(
                        f"Choose from: {', '.join(DEFAULT_LLM_MODELS)}, or an "
                        "OpenRouter model id (openrouter/<vendor>/<model>)."
                    ),
                )

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node_spec.id):
            _issue(
                report,
                "NODE_ID_INVALID",
                f"Node id {node_spec.id!r} cannot be used safely in templates.",
                path=f"{path}.id",
                node_id=node_spec.id,
                suggestion=(
                    "Use letters, numbers, and underscores; start with a "
                    "letter or underscore."
                ),
            )

        try:
            node_class = NodeRegistry.get(node_spec.type)
        except KeyError:
            close = get_close_matches(node_spec.type, known_types, n=3, cutoff=0.45)
            suggestion = (
                f"Use one of: {', '.join(close)}."
                if close
                else "Add the node module under app/nodes and register its class."
            )
            _issue(
                report,
                "UNKNOWN_NODE_TYPE",
                f"Unknown node type: {node_spec.type}",
                path=f"{path}.type",
                node_id=node_spec.id,
                suggestion=suggestion,
            )
            continue

        unknown_fields = _unknown_config_fields(node_spec, node_class)
        for field_name in unknown_fields:
            _issue(
                report,
                "UNKNOWN_NODE_CONFIG_FIELD",
                f"{node_spec.type} does not define config field {field_name!r}.",
                path=f"{path}.config.{field_name}",
                node_id=node_spec.id,
                suggestion="Remove the field or correct its spelling.",
            )

        try:
            instance = node_class(
                node_spec.id,
                node_spec.effective_config(),
                services={},
            )
            validated_config = instance.config.model_dump(mode="python")
        except ValidationError as exc:
            for error in exc.errors(include_url=False):
                location = ".".join(str(part) for part in error["loc"])
                _issue(
                    report,
                    "NODE_CONFIG_INVALID",
                    error["msg"],
                    path=f"{path}.config"
                    + (f".{location}" if location else ""),
                    node_id=node_spec.id,
                    suggestion="Match the node's Builder config schema.",
                )
        except Exception as exc:
            _issue(
                report,
                "NODE_CONSTRUCTION_FAILED",
                f"{node_spec.type} could not be constructed: {exc}",
                path=f"{path}.config",
                node_id=node_spec.id,
            )

        if node_spec.type in {"RAGAgent", "KnowledgeRetrieval"}:
            runtime_filters = validated_config.get("runtime_filters")
            if isinstance(runtime_filters, dict):
                from app.retrieval.filters import RESERVED_METADATA_FIELDS

                unsafe = sorted(set(runtime_filters) & RESERVED_METADATA_FIELDS)
                for field_name in unsafe:
                    _issue(
                        report,
                        "RAG_RUNTIME_FILTER_UNSAFE",
                        f"runtime_filters cannot set {field_name!r} — it is a "
                        "reserved security/provenance field.",
                        path=f"{path}.config.runtime_filters.{field_name}",
                        node_id=node_spec.id,
                        suggestion=(
                            "Remove this key; retrieval scope is always resolved "
                            "server-side and can only be narrowed, never widened, "
                            "by a runtime filter."
                        ),
                    )

        models = list(_iter_model_values(validated_config))
        if node_spec.selected_model:
            models.append((f"{path}.selected_model", node_spec.selected_model))
        for model_path, model_name in models:
            if model_name == AUTO_MODEL:
                # Routing sentinel — resolved to a concrete model at call time
                # by the deterministic ModelRouter, so it is not a catalog
                # entry and must not be routed here either.
                continue
            if is_openrouter_model_id(model_name):
                # Live OpenRouter catalog (app/llm/openrouter_catalog.py) — structural
                # pattern check only, no live lookup (preflight stays network-free).
                # OpenRouter itself is the authoritative source and rejects a nonexistent
                # model at dispatch time; skip resolve_model() below entirely for these —
                # it's app/llm/registry.py's fallback-chain resolver for the static catalog,
                # and openrouter/-prefixed ids are already routed via their own
                # _PREFIX_ROUTES entry (OpenRouterGateway) regardless of its result.
                continue
            if model_name not in DEFAULT_LLM_MODELS:
                _issue(
                    report,
                    "MODEL_NOT_IN_CATALOG",
                    f"Model {model_name!r} is not in the approved model catalog.",
                    path=f"{path}.{model_path}",
                    node_id=node_spec.id,
                    suggestion=(
                        f"Choose one of: {', '.join(DEFAULT_LLM_MODELS)}, or an "
                        "OpenRouter model id (openrouter/<vendor>/<model>)."
                    ),
                )
                continue
            try:
                resolve_model(model_name)
            except Exception as exc:
                _issue(
                    report,
                    "MODEL_ROUTE_INVALID",
                    f"Model {model_name!r} cannot be routed: {exc}",
                    path=f"{path}.{model_path}",
                    node_id=node_spec.id,
                )

    report.required_services = sorted(required_services)
    _add_check(
        report,
        "node_configs_and_models",
        before,
        f"{len(spec.nodes)} node instance(s) checked without execution.",
    )


def _edge_targets(edge: EdgeSpec) -> list[str]:
    targets: list[str] = []
    if isinstance(edge.to, list):
        targets.extend(edge.to)
    elif edge.to:
        targets.append(edge.to)
    targets.extend((edge.branches or {}).values())
    return targets


def _adjacency(
    spec: WorkflowSpec,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    forward = {node.id: set() for node in spec.nodes}
    reverse = {node.id: set() for node in spec.nodes}
    for edge in spec.edges:
        for target in _edge_targets(edge):
            forward[edge.from_].add(target)
            reverse[target].add(edge.from_)
    return forward, reverse


def _reachable(start: str, graph: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(graph[current] - seen)
    return seen


def _topological_order(
    reachable: set[str],
    forward: dict[str, set[str]],
    reverse: dict[str, set[str]],
) -> list[str]:
    """Kahn's-algorithm topological sort restricted to `reachable`. Nodes
    that are part of a cycle are simply never enqueued (their in-degree
    never reaches 0) and are omitted from the result — GRAPH_CYCLE already
    flags cycles separately; _guaranteed_before below treats any node
    missing from this order as "unknown, skip the check" rather than
    guessing about execution order inside a cycle.
    """
    in_degree = {n: len(reverse.get(n, set()) & reachable) for n in reachable}
    queue: deque[str] = deque(n for n in reachable if in_degree[n] == 0)
    order: list[str] = []
    seen: set[str] = set()
    while queue:
        node_id = queue.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)
        order.append(node_id)
        for succ in forward.get(node_id, set()) & reachable:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)
    return order


def _guaranteed_before(
    entry: str,
    forward: dict[str, set[str]],
    reverse: dict[str, set[str]],
) -> dict[str, set[str]]:
    """For each node reachable from entry, the set of nodes GUARANTEED to
    have already run by the time it fires.

    This is NOT classical CFG dominance (which takes the INTERSECTION of
    predecessors' dominator sets at a merge point, assuming any one
    alternative predecessor is enough to reach the merge point). This
    engine's actual join semantics are the opposite: a node with multiple
    declared predecessors is an AND-join — see app/runtime/compiler.py's
    _wire_edges, which now (as of the mixed-fan-in HITL/router race fix)
    guarantees ALL of a node's declared predecessors have completed before
    it fires, regardless of whether they arrived via a plain edge, a HITL
    dispatch, or a router branch. So the correct computation is the UNION
    of every declared direct predecessor's own guaranteed-set, not an
    intersection — a node reachable via exactly one router branch still has
    that branch's source guaranteed (only one decision fires it, and that
    decision requires the router to have run), while a node reachable via
    TWO alternative router branches of the SAME router (an actual either/or)
    only has the router itself guaranteed, not either branch-specific
    ancestor -- which the union-of-direct-predecessors computation reflects
    correctly on its own, since the router node itself is what's common to
    every one of the (at most one, in practice) matching direct edges.
    """
    reachable = _reachable(entry, forward)
    order = _topological_order(reachable, forward, reverse)
    guaranteed: dict[str, set[str]] = {}
    for node_id in order:
        acc: set[str] = {node_id}
        for pred in reverse.get(node_id, set()) & reachable:
            acc |= guaranteed.get(pred, {pred})
        guaranteed[node_id] = acc
    return guaranteed


def _has_cycle(graph: dict[str, set[str]]) -> bool:
    colors = {node_id: 0 for node_id in graph}

    def visit(node_id: str) -> bool:
        colors[node_id] = 1
        for target in graph[node_id]:
            if colors[target] == 1:
                return True
            if colors[target] == 0 and visit(target):
                return True
        colors[node_id] = 2
        return False

    return any(
        colors[node_id] == 0 and visit(node_id)
        for node_id in graph
    )


def _validate_router_edges(
    spec: WorkflowSpec,
    report: WorkflowPreflightReport,
) -> None:
    nodes = {node.id: node for node in spec.nodes}
    conditional_sources: set[str] = set()

    for index, edge in enumerate(spec.edges):
        edge_path = f"edges.{index}"
        has_to = bool(edge.to)
        has_branches = bool(edge.branches)
        if not has_to and not has_branches:
            _issue(
                report,
                "EDGE_HAS_NO_TARGET",
                f"Edge from {edge.from_!r} has no target or branches.",
                path=edge_path,
            )
        if has_to and has_branches:
            _issue(
                report,
                "EDGE_TARGET_CONFLICT",
                "An edge cannot define both 'to' and 'branches'.",
                path=edge_path,
            )
        if bool(edge.condition) != has_branches:
            _issue(
                report,
                "CONDITIONAL_EDGE_INCOMPLETE",
                "Conditional edges must define both condition and branches.",
                path=edge_path,
            )
        if not has_branches:
            continue

        if edge.from_ in conditional_sources:
            _issue(
                report,
                "MULTIPLE_CONDITIONAL_EDGES",
                f"Node {edge.from_!r} has more than one conditional edge.",
                path=edge_path,
                node_id=edge.from_,
            )
        conditional_sources.add(edge.from_)
        source = nodes[edge.from_]
        if source.type != "RouterAgent":
            try:
                source_class = NodeRegistry.get(source.type)
                output_fields = set(source_class.output_schema.model_fields)
            except Exception:
                output_fields = set()
            if "route" not in output_fields:
                _issue(
                    report,
                    "CONDITIONAL_SOURCE_HAS_NO_ROUTE",
                    f"Node {edge.from_!r} does not declare a 'route' output.",
                    path=edge_path,
                    node_id=edge.from_,
                    suggestion="Use RouterAgent or add a typed route output.",
                )
        if edge.condition != "route":
            _issue(
                report,
                "UNSUPPORTED_ROUTE_CONDITION",
                f"Compiler routes on the 'route' output, not {edge.condition!r}.",
                path=f"{edge_path}.condition",
                node_id=edge.from_,
                suggestion="Set condition: route.",
            )

        if source.type == "RouterAgent":
            config = source.effective_config()
            mode = config.get("mode", "rule")
            if mode in ("field", "conditions"):
                _validate_typed_router(
                    source, config, mode, edge, edge_path, report
                )
                continue

            rules = config.get("rules") or []
            names = [
                rule.get("name")
                for rule in rules
                if isinstance(rule, dict) and rule.get("name")
            ]
            duplicate_names = sorted(
                {name for name in names if names.count(name) > 1}
            )
            if duplicate_names:
                _issue(
                    report,
                    "ROUTER_DUPLICATE_RULE",
                    f"Router has duplicate rule names: {duplicate_names}.",
                    path=f"nodes.{source.id}.config.rules",
                    node_id=source.id,
                )
            branch_names = set(edge.branches or {})
            rule_names = set(names)
            if branch_names != rule_names:
                _issue(
                    report,
                    "ROUTER_BRANCH_MISMATCH",
                    f"Router rules {sorted(rule_names)} do not match edge "
                    f"branches {sorted(branch_names)}.",
                    path=edge_path,
                    node_id=source.id,
                    suggestion="Use the same names in rules and branches.",
                )
            defaults = sum(
                bool(rule.get("default"))
                for rule in rules
                if isinstance(rule, dict)
            )
            if defaults > 1:
                _issue(
                    report,
                    "ROUTER_MULTIPLE_DEFAULTS",
                    "Router may define at most one default rule.",
                    path=f"nodes.{source.id}.config.rules",
                    node_id=source.id,
                )
            elif defaults == 0:
                _issue(
                    report,
                    "ROUTER_NO_DEFAULT",
                    "Router has no default; unmatched input will fail at runtime.",
                    severity=PreflightSeverity.WARNING,
                    path=f"nodes.{source.id}.config.rules",
                    node_id=source.id,
                    suggestion="Add one rule with default: true.",
                )


def _validate_typed_router(
    source: NodeSpec,
    config: dict[str, Any],
    mode: str,
    edge: EdgeSpec,
    edge_path: str,
    report: WorkflowPreflightReport,
) -> None:
    """Branch/route agreement for the Builder's visual router modes.

    The legacy `rule` mode names a route per rule, so route names and branch
    names are the same list. `field` mode maps *values* to route names (several
    values may share a branch) and `conditions` mode names a route per case, so
    the comparison has to go through RouterConfig.route_names() rather than
    reading `rules`.
    """
    from app.nodes.router import RouterConfig

    try:
        parsed = RouterConfig(**config)
    except ValidationError:
        # Config validation already reported this with field-level detail; a
        # second, vaguer message here would just be noise.
        return

    route_names = parsed.route_names()
    branch_names = set(edge.branches or {})
    declared = set(route_names)

    missing_targets = sorted(declared - branch_names)
    if missing_targets:
        _issue(
            report,
            "ROUTER_BRANCH_WITHOUT_TARGET",
            f"Router can return {missing_targets} but the edge declares no "
            f"branch for them; those runs would fail at the branch.",
            path=edge_path,
            node_id=source.id,
            suggestion=(
                "Draw an edge for each branch, or remove the route from the "
                "router's configuration."
            ),
        )

    unreachable = sorted(branch_names - declared)
    if unreachable:
        _issue(
            report,
            "UNREACHABLE_BRANCH",
            f"Edge declares branch(es) {unreachable} that this router can never "
            "return.",
            severity=PreflightSeverity.WARNING,
            path=edge_path,
            node_id=source.id,
            suggestion=(
                "Rename the branch to match a configured route, or delete the "
                "edge."
            ),
        )

    if not parsed.fallback:
        _issue(
            report,
            "MISSING_DEFAULT_ROUTE",
            (
                f"Router has no fallback branch. In {mode} mode an unexpected "
                "value fails the run instead of being handled."
            ),
            severity=PreflightSeverity.WARNING,
            path=f"nodes.{source.id}.config.fallback",
            node_id=source.id,
            suggestion=(
                "Set a fallback branch — routing unclear cases to a human "
                "review step is the usual choice."
            ),
        )


def _exclusive_branch_groups(
    spec: WorkflowSpec, forward: dict[str, set[str]]
) -> list[list[set[str]]]:
    """For each router (condition+branches) edge with 2+ distinct targets,
    the node sets exclusively reachable via each individual branch target
    (i.e. reachable via THAT target but not via any OTHER target of the
    SAME router). One entry per router edge; each entry is the list of that
    router's per-branch exclusive sets. Used by _mutually_exclusive below
    to detect a fan-in target whose declared predecessors can never all
    fire together — see FANIN_UNREACHABLE_ANDJOIN.
    """
    per_router: list[list[set[str]]] = []
    for edge in spec.edges:
        if not (edge.condition and edge.branches):
            continue
        targets = list(dict.fromkeys(edge.branches.values()))
        if len(targets) < 2:
            continue
        reach_by_target = {t: _reachable(t, forward) for t in targets}
        exclusive_sets = []
        for t in targets:
            others: set[str] = set()
            for t2 in targets:
                if t2 != t:
                    others |= reach_by_target[t2]
            exclusive_sets.append(reach_by_target[t] - others)
        per_router.append(exclusive_sets)
    return per_router


def _mutually_exclusive(
    a: str, b: str, exclusive_groups: list[list[set[str]]]
) -> bool:
    """True if `a` and `b` are exclusively reachable via two DIFFERENT
    branches of the SAME router — i.e. provably can never both execute in
    the same run. Being merely "incomparable" (neither reaches the other)
    is NOT sufficient on its own: two parallel branches of an ordinary
    unconditional fan-out (`to: [a, b]`) are also incomparable but DO both
    always run together — confirmed this is the common case in this
    codebase's own shipped workflows (dozens of legitimate hits), so this
    function deliberately requires tracing back to a shared conditional
    (router) ancestor with differing branch membership, not just
    reachability incomparability.
    """
    for exclusive_sets in exclusive_groups:
        group_a = next((s for s in exclusive_sets if a in s), None)
        group_b = next((s for s in exclusive_sets if b in s), None)
        if group_a is not None and group_b is not None and group_a is not group_b:
            return True
    return False


def _validate_fanin_reachability(
    spec: WorkflowSpec,
    report: WorkflowPreflightReport,
    forward: dict[str, set[str]],
) -> None:
    """A plain-edge fan-in target (2+ declared predecessors combined into
    one add_edge([...], target) AND-join — see app/runtime/compiler.py's
    _wire_edges) can only ever fire if ALL of its predecessors actually run
    in the same invocation. If two of them are provably mutually exclusive
    branches of the same upstream router, that can never happen — the
    target silently never fires (LangGraph doesn't error or hang; the
    invocation just completes without ever running it or anything
    downstream of it). Confirmed empirically against real langgraph 1.2.9
    execution before writing this check, precisely because it doesn't
    manifest as a "Template path not resolvable" KeyError at all -- the
    referencing node just never gets a chance to run its template.
    """
    hitl_ids = {n.id for n in spec.nodes if n.type == "HumanInLoopAgent"}
    plain_targets: dict[str, list[str]] = {}
    for edge in spec.edges:
        if edge.from_ in hitl_ids and not (edge.condition and edge.branches):
            continue
        if edge.condition and edge.branches:
            continue
        targets = edge.to if isinstance(edge.to, list) else ([edge.to] if edge.to else [])
        for target in targets:
            plain_targets.setdefault(target, []).append(edge.from_)

    exclusive_groups = _exclusive_branch_groups(spec, forward)
    if not exclusive_groups:
        return  # no routers at all -> nothing can be mutually exclusive

    for target, preds in plain_targets.items():
        uniq = list(dict.fromkeys(preds))
        if len(uniq) < 2:
            continue
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = uniq[i], uniq[j]
                if _mutually_exclusive(a, b, exclusive_groups):
                    _issue(
                        report,
                        "FANIN_UNREACHABLE_ANDJOIN",
                        f"Node {target!r} requires BOTH {a!r} and {b!r} to "
                        "complete (a plain-edge fan-in is an AND-join), but "
                        "they are mutually exclusive branches of an "
                        f"upstream router — {target!r} can never fire.",
                        node_id=target,
                        suggestion=(
                            "Route both branches to the same node instead of "
                            "separate ones that later reconverge via plain "
                            "edges, or restructure so the router's own "
                            "branches map directly to the shared target."
                        ),
                    )


def _validate_graph(
    spec: WorkflowSpec,
    report: WorkflowPreflightReport,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    before = len(report.issues)
    _validate_router_edges(spec, report)
    forward, reverse = _adjacency(spec)
    entry = spec.entry or spec.nodes[0].id
    reachable = _reachable(entry, forward)
    _validate_fanin_reachability(spec, report, forward)

    for node_id in sorted(set(forward) - reachable):
        _issue(
            report,
            "UNREACHABLE_NODE",
            f"Node {node_id!r} cannot be reached from entry {entry!r}.",
            node_id=node_id,
            suggestion="Connect it to the graph or remove it.",
        )

    explicit_exits = (
        {spec.exit}
        if isinstance(spec.exit, str)
        else set(spec.exit or [])
    )
    terminal_nodes = explicit_exits or {
        node_id for node_id, targets in forward.items() if not targets
    }
    if not terminal_nodes:
        _issue(
            report,
            "NO_TERMINAL_NODE",
            "Workflow has no exit and every node has an outgoing edge.",
            suggestion="Declare exit or provide a path that reaches a terminal node.",
        )
    else:
        can_reach_terminal: set[str] = set()
        queue = deque(terminal_nodes)
        while queue:
            current = queue.popleft()
            if current in can_reach_terminal:
                continue
            can_reach_terminal.add(current)
            queue.extend(reverse[current] - can_reach_terminal)
        for node_id in sorted(reachable - can_reach_terminal):
            _issue(
                report,
                "NO_EXIT_PATH",
                f"Node {node_id!r} cannot reach any workflow exit.",
                node_id=node_id,
            )

    seen_edges: set[tuple[str, str, str]] = set()
    for index, edge in enumerate(spec.edges):
        label = "conditional" if edge.branches else "plain"
        for target in _edge_targets(edge):
            key = (edge.from_, target, label)
            if key in seen_edges and not edge.branches:
                _issue(
                    report,
                    "DUPLICATE_EDGE",
                    f"Duplicate edge {edge.from_!r} -> {target!r}.",
                    path=f"edges.{index}",
                )
            seen_edges.add(key)

    if _has_cycle(forward):
        _issue(
            report,
            "GRAPH_CYCLE",
            "Workflow contains a cycle; static preflight cannot prove it terminates.",
            severity=PreflightSeverity.WARNING,
            suggestion="Keep the cycle only if a deterministic stop condition exists.",
        )

    _add_check(
        report,
        "graph_topology",
        before,
        f"Entry {entry!r}; {len(terminal_nodes)} terminal/exit node(s).",
    )
    return forward, reverse


def _iter_strings(
    value: Any,
    path: str,
) -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_strings(child, f"{path}.{index}")


def _annotation_permits_none(annotation: Any) -> bool:
    """True if a declared field type allows None (``X | None``/Optional[X]).

    Such a field can hold a value that cannot be traversed by a nested
    template path, even though the key itself is present in node_outputs.
    A bare ``None`` annotation counts; ``Any`` deliberately does not, since
    it carries no claim about nullability either way.
    """
    if annotation is None or annotation is type(None):
        return True
    return any(arg is type(None) for arg in get_args(annotation))


def _validate_template_output_path(
    spec: WorkflowSpec,
    current_node: NodeSpec,
    reference: str,
    report: WorkflowPreflightReport,
    path: str,
    forward: dict[str, set[str]],
    guaranteed: dict[str, set[str]],
) -> None:
    parts = reference.split(".")
    first = parts[0]
    node_map = {node.id: node for node in spec.nodes}
    input_names = set(spec.inputs)
    variable_names = {item.name for item in spec.static_variables}

    if first == "inputs":
        if len(parts) < 2 or parts[1] not in input_names:
            _issue(
                report,
                "TEMPLATE_UNKNOWN_INPUT",
                f"Template references unknown input {reference!r}.",
                path=path,
                node_id=current_node.id,
            )
        return
    if first == "variables":
        if len(parts) < 2 or parts[1] not in variable_names:
            _issue(
                report,
                "TEMPLATE_UNKNOWN_VARIABLE",
                f"Template references unknown variable {reference!r}.",
                path=path,
                node_id=current_node.id,
            )
        return
    if first in {"outputs", "node_outputs"}:
        if len(parts) < 2:
            _issue(
                report,
                "TEMPLATE_UNKNOWN_NODE",
                f"Template output reference {reference!r} must include a node id.",
                path=path,
                node_id=current_node.id,
                suggestion="Use {{outputs.<node_id>.<field>}}.",
            )
            return
        parts = parts[1:]
        first = parts[0]
    if first in {
        "session_id",
        "collection_id",
        "domain_state",
        "workflow_id",
        "workflow_name",
    }:
        return
    if first not in node_map:
        close = get_close_matches(first, sorted(node_map), n=3, cutoff=0.5)
        _issue(
            report,
            "TEMPLATE_UNKNOWN_NODE",
            f"Template references unknown node/path {reference!r}.",
            path=path,
            node_id=current_node.id,
            suggestion=(
                f"Did you mean {', '.join(close)}?" if close else None
            ),
        )
        return
    if first == current_node.id:
        _issue(
            report,
            "TEMPLATE_SELF_REFERENCE",
            f"Node {current_node.id!r} cannot read its own output before it runs.",
            path=path,
            node_id=current_node.id,
        )
        return

    not_upstream = current_node.id not in _reachable(first, forward)
    if not_upstream:
        _issue(
            report,
            "TEMPLATE_NOT_UPSTREAM",
            f"Template reads {first!r}, but that node cannot execute before "
            f"{current_node.id!r}.",
            path=path,
            node_id=current_node.id,
            suggestion="Add the required upstream edge or fix the template.",
        )
    elif first not in guaranteed.get(current_node.id, set()):
        # Reachable via SOME path, but not every path -- e.g. current_node is
        # a merge point reachable both from this router branch and from a
        # sibling branch that never runs `first` at all. If the OTHER branch
        # is taken at runtime, `first` never executes and this reference
        # fails with "Template path not resolvable" -- the exact error class
        # this whole check exists to catch before any node runs. Silent if
        # current_node isn't in `guaranteed` at all (inside a graph cycle;
        # GRAPH_CYCLE already flags that separately and this check doesn't
        # attempt to reason about execution order inside one).
        if current_node.id in guaranteed:
            _issue(
                report,
                "TEMPLATE_CONDITIONAL_UPSTREAM",
                f"Template reads {first!r}, but {first!r} only executes on "
                f"SOME paths that reach {current_node.id!r} (e.g. one branch "
                "of a router), not every path. If a different branch is "
                f"taken at runtime, {first!r} never runs and this reference "
                "fails.",
                path=path,
                node_id=current_node.id,
                suggestion=(
                    "Reference a node common to every path instead (e.g. a "
                    "shared ancestor before the branch point), or move this "
                    "reference to a node only reachable via the same branch "
                    f"as {first!r}."
                ),
            )

    if len(parts) < 2:
        return
    try:
        source_class = NodeRegistry.get(node_map[first].type)
    except KeyError:
        return
    # `preflight_output_fields` is the generic extension point (app/nodes/base.py):
    # most node types just return their static output_schema field names, but a
    # node type can override it to allow nested dotted references (e.g. a
    # declared structured sub-schema) — no per-node-type dispatch lives here.
    fields = source_class.preflight_output_fields(node_map[first].effective_config())
    top_level = {field.split(".", 1)[0] for field in fields}
    if parts[1] not in top_level:
        _issue(
            report,
            "TEMPLATE_UNKNOWN_OUTPUT_FIELD",
            f"{node_map[first].type} {first!r} has no output field "
            f"{parts[1]!r}.",
            path=path,
            node_id=current_node.id,
            suggestion=f"Available fields: {', '.join(sorted(top_level))}.",
        )
        return

    # A bare reference to a field whose value preflight can already prove
    # (e.g. TransformAgent's "parsed" is always {} when its config sets no
    # output_schema) can only ever substitute that one fixed value — never
    # real content — regardless of what the upstream node actually produces
    # at runtime. This is virtually always an authoring mistake, so it's an
    # error here rather than left to fail deep inside a downstream node's
    # Pydantic validation after the (possibly costly) upstream call already ran.
    static_values = source_class.preflight_static_output_values(
        node_map[first].effective_config()
    )
    if parts[1] in static_values:
        _issue(
            report,
            "TEMPLATE_STATICALLY_EMPTY_FIELD",
            f"{node_map[first].type} {first!r}'s {parts[1]!r} output is "
            f"always {static_values[parts[1]]!r} given its current "
            "config — substituting it here can only ever produce that "
            "fixed value, never real content.",
            path=path,
            node_id=current_node.id,
            suggestion=(
                "Use a different output field (e.g. '.raw' for a "
                "TransformAgent with no output_schema), or add the "
                "config that would actually populate this field."
            ),
        )
        return

    # Nested traversal through a field whose declared type permits None.
    #
    # `{{gate.content.text}}` resolves `content` and then indexes into it. If
    # the declared annotation is `X | None` and the node returns None on some
    # path (e.g. HumanInLoopAgent's `content` is None when no context field
    # resolves to a value), _lookup fails with "Template path not resolvable
    # ... <not a dict: NoneType=None>". The compiler now materialises declared
    # defaults into node_outputs, so the KEY is always present — but a None
    # default still cannot be traversed, which is why this needs its own
    # check rather than being covered by that fix.
    #
    # WARNING, not an error: whether the field is actually None depends on
    # runtime data, and these references are usually correct in practice
    # (shipped workflows rely on them). Blocking them would reject valid
    # workflows; staying silent hides a real, hard-to-diagnose mid-run
    # failure. So it is surfaced without failing preflight.
    if len(parts) >= 3:
        output_schema = getattr(source_class, "output_schema", None)
        field_info = (
            output_schema.model_fields.get(parts[1])
            if output_schema is not None
            else None
        )
        if field_info is not None and _annotation_permits_none(
            field_info.annotation
        ):
            _issue(
                report,
                "TEMPLATE_NULLABLE_NESTED_ACCESS",
                f"Template traverses into {parts[1]!r} of "
                f"{node_map[first].type} {first!r}, but that field's declared "
                f"type permits None. If it is None at runtime, "
                f"{reference!r} fails mid-run.",
                severity=PreflightSeverity.WARNING,
                path=path,
                node_id=current_node.id,
                suggestion=(
                    f"Confirm {first!r} always populates {parts[1]!r} on every "
                    "path that reaches this node, or reference a "
                    "non-nullable field instead."
                ),
            )

    # Nested dotted access (node.field.subfield) is only checked for node
    # types that explicitly opt in by overriding preflight_output_fields
    # (e.g. TransformAgent, whose "parsed" field is a free-form dict shaped
    # by the node's own YAML config). Everything else keeps its statically
    # typed nested fields (e.g. a Pydantic sub-model) unchecked here, exactly
    # like the top-level output_schema check already trusts Pydantic's shape
    # — there is no way to know a type-annotated nested field is wrong
    # without re-deriving its whole schema, so we don't guess.
    overrides_structure_check = (
        source_class.preflight_output_fields.__func__
        is not NodeType.preflight_output_fields.__func__
    )
    if not overrides_structure_check:
        return

    remainder = ".".join(parts[1:])
    if len(parts) >= 3 and not _matches_declared_field(remainder, fields):
        _issue(
            report,
            "TEMPLATE_UNKNOWN_STRUCTURED_FIELD",
            f"{node_map[first].type} {first!r} does not declare structured "
            f"field {remainder!r}.",
            path=path,
            node_id=current_node.id,
            suggestion=(
                "Add it to the node's declared output schema or correct "
                "the template path."
            ),
        )


def _matches_declared_field(reference: str, declared: set[str]) -> bool:
    """Does a dotted reference match one of a node's declared output paths?

    Exact match, plus two extensions that exist because some output shapes are
    genuinely not knowable at preflight time:

    *   **`prefix.*`** — "anything under here". An MCP tool's result shape is
        defined by the *server*, which preflight deliberately does not contact
        (a Builder check must not depend on a CRM being reachable). The node
        declares `data.*`; the Builder's discovery panel, which can reach the
        server, is what validates the sub-path properly.
    *   **numeric segments** — a list index (`accounts.0.account_id`) matches a
        declaration written without it (`accounts.account_id`), since the
        declared schema describes the item shape, not each position.
    """
    if reference in declared:
        return True

    without_indices = ".".join(
        part for part in reference.split(".") if not part.lstrip("-").isdigit()
    )
    if without_indices in declared:
        return True
    # `items` is the convention field_schema and results use for a list's
    # element shape, so `accounts.0.id` should also match `accounts.items.id`.
    segments = reference.split(".")
    with_items = ".".join(
        "items" if part.lstrip("-").isdigit() else part for part in segments
    )
    if with_items in declared:
        return True

    for entry in declared:
        if entry.endswith(".*") and (
            reference.startswith(entry[:-1]) or reference == entry[:-2]
        ):
            return True
    return False


def _validate_templates(
    spec: WorkflowSpec,
    report: WorkflowPreflightReport,
    forward: dict[str, set[str]],
    reverse: dict[str, set[str]],
) -> None:
    before = len(report.issues)
    entry = spec.entry or spec.nodes[0].id
    guaranteed = _guaranteed_before(entry, forward, reverse)
    for index, node in enumerate(spec.nodes):
        config = node.effective_config()
        for path, text in _iter_strings(config, f"nodes.{index}.config"):
            if text.count("{{") != text.count("}}"):
                _issue(
                    report,
                    "TEMPLATE_UNBALANCED",
                    "Template braces are unbalanced.",
                    path=path,
                    node_id=node.id,
                )
                continue
            for match in TEMPLATE_RE.finditer(text):
                _validate_template_output_path(
                    spec,
                    node,
                    match.group(1),
                    report,
                    path,
                    forward,
                    guaranteed,
                )

    _add_check(
        report,
        "templates",
        before,
        "Template roots, fields, and upstream order checked.",
    )


def _validate_inputs(
    spec: WorkflowSpec,
    provided_inputs: dict[str, Any] | None,
    report: WorkflowPreflightReport,
) -> None:
    before = len(report.issues)
    if provided_inputs is None:
        report.checks.append(
            PreflightCheck(
                name="run_inputs",
                status="skipped",
                detail="No run inputs were supplied for structural validation.",
            )
        )
        return

    for name, input_spec in spec.inputs.items():
        value = provided_inputs.get(name)
        missing = value is None or value == "" or value == []
        if input_spec.required and missing:
            _issue(
                report,
                "REQUIRED_INPUT_MISSING",
                f"Required workflow input {name!r} is missing.",
                path=f"inputs.{name}",
            )
            continue
        if missing:
            continue
        if input_spec.type == "text" and not isinstance(value, str):
            _issue(
                report,
                "INPUT_TYPE_MISMATCH",
                f"Input {name!r} must be text.",
                path=f"inputs.{name}",
            )
        if input_spec.type == "file":
            values = value if isinstance(value, list) else [value]
            if not input_spec.multiple and len(values) != 1:
                _issue(
                    report,
                    "FILE_INPUT_MULTIPLICITY",
                    f"Input {name!r} accepts exactly one file.",
                    path=f"inputs.{name}",
                )
            for item in values:
                if not isinstance(item, dict) or item.get("kind") != "workflow_file":
                    _issue(
                        report,
                        "FILE_INPUT_REFERENCE_INVALID",
                        f"Input {name!r} must contain uploaded workflow-file references.",
                        path=f"inputs.{name}",
                    )
                    break

    for name in sorted(set(provided_inputs) - set(spec.inputs)):
        if not name.startswith("SYSTEM."):
            _issue(
                report,
                "UNDECLARED_INPUT",
                f"Input {name!r} is not declared by the workflow.",
                severity=PreflightSeverity.WARNING,
                path=f"inputs.{name}",
                suggestion="Remove it or declare it under workflow inputs.",
            )

    _add_check(
        report,
        "run_inputs",
        before,
        f"{len(provided_inputs)} supplied input(s) checked.",
    )


def _compile_dry_run(
    spec: WorkflowSpec,
    services: dict[str, Any] | None,
    report: WorkflowPreflightReport,
) -> None:
    before = len(report.issues)
    if report.errors:
        report.checks.append(
            PreflightCheck(
                name="graph_compile",
                status="skipped",
                detail="Compilation skipped until earlier errors are fixed.",
            )
        )
        return
    try:
        from app.runtime.compiler import compile_workflow

        compile_workflow(spec, services=services or {})
    except Exception as exc:
        _issue(
            report,
            "GRAPH_COMPILE_FAILED",
            f"LangGraph dry compile failed: {type(exc).__name__}: {exc}",
            suggestion="Fix the node/edge identified by the compiler.",
        )
    _add_check(
        report,
        "graph_compile",
        before,
        "LangGraph compiled without executing a node.",
    )


def _validate_guided_experience(
    spec: WorkflowSpec,
    report: WorkflowPreflightReport,
) -> None:
    """Validate authored Guided Run copy without penalising legacy workflows.

    Existing YAMLs remain runnable through deterministic frontend fallbacks.
    Once an author adds an ``experience`` block to a user-visible node, the
    block becomes a publication contract and must contain enough plain-language
    information to explain the work, its handoff, and a safe failure state.
    """

    before = len(report.issues)
    required_fields = {
        "display_name": "a business-step name",
        "purpose": "why the step exists",
        "contribution": "how its result helps later work",
        "expected_output": "the user-visible result",
        "failure_message": "a practical failure explanation",
    }
    for node in spec.nodes:
        experience = node.experience
        if experience is None or experience.visibility == "advanced":
            continue
        for field_name, description in required_fields.items():
            value = getattr(experience, field_name)
            if isinstance(value, str) and value.strip():
                continue
            _issue(
                report,
                "GUIDED_EXPERIENCE_INCOMPLETE",
                f"Guided Run step {node.id!r} is missing {description}.",
                node_id=node.id,
                path=f"nodes.{node.id}.experience.{field_name}",
                suggestion=(
                    "Complete the Guided tab in the Builder, or set visibility "
                    "to advanced when ordinary users should not see this step."
                ),
            )
        display_name = (experience.display_name or "").strip()
        if display_name and re.search(
            r"[_/]|(agent|node|\bllm\b|\bapi\b|payload|stack trace)",
            display_name,
            flags=re.IGNORECASE,
        ):
            _issue(
                report,
                "GUIDED_COPY_TECHNICAL",
                f"Guided Run name {display_name!r} contains technical language.",
                severity=PreflightSeverity.WARNING,
                node_id=node.id,
                path=f"nodes.{node.id}.experience.display_name",
                suggestion="Use a short business action such as 'Map the call requirements'.",
            )
        if experience.show_agent_role and not (experience.agent_role or "").strip():
            _issue(
                report,
                "GUIDED_ROLE_MISSING",
                f"Guided Run step {node.id!r} is configured to show an empty role.",
                node_id=node.id,
                path=f"nodes.{node.id}.experience.agent_role",
                suggestion="Name the responsibility/review role or hide it.",
            )

    _add_check(
        report,
        "guided_experience",
        before,
        "Authored Guided Run steps contain understandable purpose, handoff, output, and recovery copy.",
    )


def preflight_workflow_yaml(
    yaml_text: str,
    *,
    provided_inputs: dict[str, Any] | None = None,
    services: dict[str, Any] | None = None,
    compile_graph: bool = True,
) -> WorkflowPreflightReport:
    """Run all deterministic checks. This function never calls a node or LLM."""

    report = WorkflowPreflightReport(valid=False)
    spec = _parse_spec(yaml_text, report)
    if spec is None:
        return report.refresh()

    _validate_registry(report)
    _validate_nodes(spec, report)
    forward, reverse = _validate_graph(spec, report)
    _validate_templates(spec, report, forward, reverse)
    _validate_inputs(spec, provided_inputs, report)
    _validate_guided_experience(spec, report)
    _validate_business_logic(spec, report, forward, reverse)
    if compile_graph:
        _compile_dry_run(spec, services, report)
    return report.refresh()


def _validate_business_logic(
    spec: WorkflowSpec,
    report: WorkflowPreflightReport,
    forward: dict[str, set[str]],
    reverse: dict[str, set[str]],
) -> None:
    """Check visually authored rules, routes and schemas (zero tokens).

    Delegated to app/runtime/logic_preflight.py: those checks need the typed
    field index built from node configs, which is the same index the Builder's
    mapping picker and rule editor read. Keeping them in one module is what stops
    the editor from offering an operator preflight would then reject.
    """
    from app.runtime.logic_preflight import validate_business_logic

    before = len(report.issues)

    def record(
        code: str,
        message: str,
        *,
        severity: str = "error",
        path: str | None = None,
        node_id: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        _issue(
            report,
            code,
            message,
            severity=(
                PreflightSeverity.WARNING
                if severity == "warning"
                else PreflightSeverity.ERROR
            ),
            path=path,
            node_id=node_id,
            suggestion=suggestion,
        )

    # Reuse the AND-join-aware ordering the template checks already rely on, so
    # "this value is always available here" means the same thing to a rule as it
    # does to a template reference.
    entry = spec.entry or (spec.nodes[0].id if spec.nodes else "")
    try:
        guaranteed = _guaranteed_before(entry, forward, reverse) if entry else {}
    except Exception:
        guaranteed = {}

    def always_before(source: str, target: str) -> bool:
        # An unknown target means the graph checks already reported a problem
        # with it; don't pile a second, less useful message on top.
        if target not in guaranteed:
            return True
        return source in guaranteed[target]

    try:
        validate_business_logic(
            spec,
            issue=record,
            guaranteed_before=always_before,
        )
    except Exception as exc:
        # A crash in a *validator* must not block a workflow that may be fine.
        # Report it as a warning so the gap is visible rather than silent.
        _issue(
            report,
            "LOGIC_CHECK_FAILED",
            f"Business-logic checks could not complete: {type(exc).__name__}: {exc}",
            severity=PreflightSeverity.WARNING,
            suggestion="The workflow may still be valid; report this diagnostic.",
        )

    _add_check(
        report,
        "business_logic",
        before,
        "Rules, routes and output schemas checked against the workflow's own "
        "typed contracts without spending tokens.",
    )


def preflight_workflow_spec(
    spec: WorkflowSpec,
    *,
    provided_inputs: dict[str, Any] | None = None,
    services: dict[str, Any] | None = None,
    compile_graph: bool = False,
) -> WorkflowPreflightReport:
    """Validate an already parsed spec without changing or running it."""

    payload = yaml.safe_dump(
        spec.model_dump(by_alias=True, exclude_none=True),
        sort_keys=False,
    )
    return preflight_workflow_yaml(
        payload,
        provided_inputs=provided_inputs,
        services=services,
        compile_graph=compile_graph,
    )


async def _probe_services(
    spec: WorkflowSpec,
    services: dict[str, Any],
    report: WorkflowPreflightReport,
    *,
    require_run_history: bool,
    owner_scope_id: str | None = None,
) -> None:
    before = len(report.issues)
    required = set(report.required_services)
    if require_run_history:
        required.update({"audit_db", "event_bus"})
    report.required_services = sorted(required)

    for service_name in sorted(required):
        if services.get(service_name) is None:
            _issue(
                report,
                "REQUIRED_SERVICE_MISSING",
                f"Required service {service_name!r} is unavailable.",
                path=f"services.{service_name}",
                suggestion=(
                    "Start the required Docker/service dependency and restart the API."
                ),
            )

    store = services.get("object_store")
    if "object_store" in required and store is not None:
        try:
            await asyncio.to_thread(store.client.list_buckets)
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            error = (
                response.get("Error", {})
                if isinstance(response, dict)
                else {}
            )
            error_code = error.get("Code")
            if error_code in {
                "InvalidAccessKeyId",
                "SignatureDoesNotMatch",
                "AccessDenied",
            }:
                _issue(
                    report,
                    "OBJECT_STORE_CREDENTIALS_INVALID",
                    "MinIO is reachable but rejected the configured "
                    f"credentials ({error_code}).",
                    path="services.object_store",
                    suggestion=(
                        "Make MINIO_ACCESS_KEY and MINIO_SECRET_KEY identical "
                        "for the API and MinIO service, remove conflicting "
                        "values from .env.local, then restart both containers."
                    ),
                )
            else:
                _issue(
                    report,
                    "OBJECT_STORE_UNAVAILABLE",
                    "Object storage is configured but not reachable: "
                    f"{type(exc).__name__}.",
                    path="services.object_store",
                    suggestion=(
                        "Start MinIO and verify MINIO_ENDPOINT, network, "
                        "bucket, and credentials."
                    ),
                )

    for service_name in sorted(required):
        if not service_name.startswith("llm:local-"):
            continue
        probe = services.get(service_name)
        if probe is None or not callable(probe):
            continue
        try:
            await asyncio.wait_for(
                probe(),
                timeout=settings.health_probe_timeout_seconds,
            )
        except Exception as exc:
            _issue(
                report,
                "LOCAL_MODEL_UNAVAILABLE",
                f"Local model {service_name.removeprefix('llm:')!r} "
                f"did not pass its endpoint probe: {type(exc).__name__}.",
                path=f"services.{service_name}",
                suggestion=(
                    "Start the configured vLLM/SGLang endpoint and verify "
                    "its served model name."
                ),
            )

    audit_db = services.get("audit_db")
    if require_run_history and audit_db is not None:
        try:
            await audit_db.command("ping")
        except Exception as exc:
            _issue(
                report,
                "RUN_HISTORY_UNAVAILABLE",
                f"Run-history database did not respond: {exc}",
                path="services.audit_db",
                suggestion="Start MongoDB and restart the API.",
            )

    mcp = services.get("mcp_client")
    if "mcp_client" in required and mcp is not None:
        for node in spec.nodes:
            if node.type not in {
                "MCPAgent",
                "ScholarlyCandidateDiscoveryAgent",
            }:
                continue
            server = (
                node.effective_config().get("mcp_server")
                if node.type == "ScholarlyCandidateDiscoveryAgent"
                else "eurskem"
            ) or "eurskem"
            if hasattr(mcp, "has_server") and not mcp.has_server(server):
                _issue(
                    report,
                    "MCP_SERVER_UNAVAILABLE",
                    f"MCP server {server!r} required by {node.id!r} is not running.",
                    node_id=node.id,
                    path=f"nodes.{node.id}.config.mcp_server",
                )
                continue
            try:
                tools = await mcp.list_tools(server=server)
            except Exception as exc:
                _issue(
                    report,
                    "MCP_SERVER_PROBE_FAILED",
                    f"Could not list tools from MCP server {server!r}: {exc}",
                    node_id=node.id,
                )
                continue
            configured_tool = None
            if node.type == "ScholarlyCandidateDiscoveryAgent":
                configured_tool = node.effective_config().get("tool")
            if configured_tool and configured_tool not in {
                getattr(tool, "name", None) for tool in tools
            }:
                _issue(
                    report,
                    "MCP_TOOL_MISSING",
                    f"MCP server {server!r} does not expose "
                    f"tool {configured_tool!r}.",
                    node_id=node.id,
                    path=f"nodes.{node.id}.config.tool",
                )

    web_search = services.get("web_search")
    if "web_search" in required and web_search is not None:
        _probe_web_search_nodes(spec, web_search, report)

    image_generator = services.get("image_generator")
    if "image_generator" in required and image_generator is not None:
        _probe_image_generation_nodes(spec, image_generator, report)

    kimi_vision = services.get("kimi_vision")
    if "kimi_vision" in required and kimi_vision is not None:
        _probe_kimi_vision_nodes(spec, kimi_vision, report)

    knowledge_repository = services.get("knowledge_repository")
    if knowledge_repository is not None and owner_scope_id:
        await _probe_rag_resource_nodes(
            spec, knowledge_repository, report, owner_scope_id=owner_scope_id
        )

    llm = services.get("llm")
    if "llm" in required and llm is not None:
        await _probe_workflow_model_access(spec, llm, report)

    _add_check(
        report,
        "required_services",
        before,
        f"{len(required)} required service(s) checked without an LLM call.",
    )


def _probe_web_search_nodes(
    spec: WorkflowSpec,
    service: Any,
    report: WorkflowPreflightReport,
) -> None:
    """Zero-token: WebSearchService.resolve_provider does a settings check
    only, no HTTP request — see app/tools/web_io.py."""

    before = len(report.issues)
    for node in spec.nodes:
        if node.type != "WebSearchAgent":
            continue
        provider = node.effective_config().get("provider", "auto")
        try:
            service.resolve_provider(provider)
        except Exception as exc:
            _issue(
                report,
                "WEB_SEARCH_PROVIDER_UNAVAILABLE",
                f"WebSearchAgent {node.id!r} cannot resolve provider "
                f"{provider!r}: {exc}",
                node_id=node.id,
                path=f"nodes.{node.id}.config.provider",
                suggestion=(
                    "Configure TAVILY_API_KEY, OPENAI_API_KEY, or "
                    "LOCAL_KIMI_API_KEY, or choose a different provider."
                ),
            )
    _add_check(
        report,
        "web_search_credentials",
        before,
        "Web-search provider availability checked without a live request.",
    )


def _probe_image_generation_nodes(
    spec: WorkflowSpec,
    service: Any,
    report: WorkflowPreflightReport,
) -> None:
    """Zero-token: OpenAIImageGenerationService.available() checks settings
    only — see app/tools/image_io.py."""

    before = len(report.issues)
    for node in spec.nodes:
        if node.type != "OpenAIImageGenerationAgent":
            continue
        if node.effective_config().get("backend", "openai") == "disabled":
            continue
        if not service.available():
            _issue(
                report,
                "IMAGE_GENERATION_UNAVAILABLE",
                f"OpenAIImageGenerationAgent {node.id!r} has no configured "
                "image-generation backend.",
                node_id=node.id,
                path=f"nodes.{node.id}.config.backend",
                suggestion=(
                    "Set OPENAI_API_KEY and IMAGE_GENERATION_BACKEND=openai, "
                    "or set this node's backend to 'disabled'."
                ),
            )
    _add_check(
        report,
        "image_generation_credentials",
        before,
        "Image-generation credentials checked without a live request.",
    )


def _probe_kimi_vision_nodes(
    spec: WorkflowSpec,
    service: Any,
    report: WorkflowPreflightReport,
) -> None:
    """Zero-token: KimiVisionService.available() checks settings only —
    see app/tools/vision_io.py."""

    before = len(report.issues)
    for node in spec.nodes:
        if node.type != "KimiVisionAgent":
            continue
        if not service.available():
            _issue(
                report,
                "KIMI_VISION_UNAVAILABLE",
                f"KimiVisionAgent {node.id!r} has no configured Moonshot "
                "credentials.",
                node_id=node.id,
                suggestion="Set LOCAL_KIMI_API_KEY.",
            )
    _add_check(
        report,
        "kimi_vision_credentials",
        before,
        "Kimi vision credentials checked without a live request.",
    )


async def _probe_rag_resource_nodes(
    spec: WorkflowSpec,
    repository: Any,
    report: WorkflowPreflightReport,
    *,
    owner_scope_id: str,
) -> None:
    """Confirm every referenced Knowledge resource exists and is usable.

    Zero-token: Mongo reads only — no embedding call, no retrieval, no LLM.
    Catches the failures that would otherwise surface mid-run as a node error:
    a deleted RAG Agent, a collection with nothing activated, a retrieval
    profile that was never saved.
    """
    from app.knowledge.models import ProfileType, ResourceStatus
    from app.knowledge.repository import ResourceNotFoundError

    before = len(report.issues)
    probed = 0

    async def _collection_ok(collection_id: str, node_id: str, path: str) -> None:
        try:
            collection = await repository.get_collection(owner_scope_id, collection_id)
        except ResourceNotFoundError:
            _issue(
                report,
                "COLLECTION_NOT_FOUND",
                f"collection {collection_id!r} does not exist in this workspace.",
                path=path,
                node_id=node_id,
                suggestion="Pick a Collection from Knowledge Studio.",
            )
            return
        if not collection.active_index_id:
            _issue(
                report,
                "COLLECTION_NOT_READY",
                f"collection {collection.name!r} has no active index, so it "
                "cannot be searched.",
                path=path,
                node_id=node_id,
                suggestion=(
                    "Run an ingestion and activate an index in "
                    "Knowledge Studio → Documents & Indexes."
                ),
            )

    for node in spec.nodes:
        if node.type not in {"RAGAgent", "KnowledgeRetrieval"}:
            continue
        config = node.config or {}
        path = f"nodes.{node.id}.config"

        agent_id = config.get("rag_agent_id")
        if agent_id:
            probed += 1
            try:
                agent = await repository.get_rag_agent(owner_scope_id, str(agent_id))
            except ResourceNotFoundError:
                _issue(
                    report,
                    "RAG_AGENT_NOT_FOUND",
                    f"RAG Agent {agent_id!r} does not exist in this workspace.",
                    path=f"{path}.rag_agent_id",
                    node_id=node.id,
                    suggestion=(
                        "Pick a saved RAG Agent in Knowledge Studio → "
                        "Profiles & RAG Agents, and copy its rag_agent_id."
                    ),
                )
            else:
                if getattr(agent, "status", None) not in {
                    ResourceStatus.ACTIVE,
                    ResourceStatus.READY,
                }:
                    _issue(
                        report,
                        "RAG_AGENT_INACTIVE",
                        f"RAG Agent {agent.name!r} is {agent.status}, not active.",
                        path=f"{path}.rag_agent_id",
                        node_id=node.id,
                        suggestion="Reactivate the agent or select another.",
                    )
                if getattr(agent, "collection_id", None):
                    await _collection_ok(
                        agent.collection_id, node.id, f"{path}.rag_agent_id"
                    )

        collection_id = config.get("collection_id")
        if collection_id:
            probed += 1
            await _collection_ok(str(collection_id), node.id, f"{path}.collection_id")

        profile_id = config.get("retrieval_profile_id")
        if profile_id:
            probed += 1
            try:
                await repository.get_profile(
                    owner_scope_id,
                    str(profile_id),
                    config.get("retrieval_profile_version"),
                    ProfileType.RETRIEVAL,
                )
            except ResourceNotFoundError:
                _issue(
                    report,
                    "RETRIEVAL_PROFILE_NOT_FOUND",
                    f"retrieval profile {profile_id!r} does not exist in this workspace.",
                    path=f"{path}.retrieval_profile_id",
                    node_id=node.id,
                    suggestion="Save one from the Retrieval Playground first.",
                )

    if probed:
        _add_check(
            report,
            "knowledge_resources",
            before,
            f"{probed} Knowledge resource reference(s) checked without a retrieval call.",
        )


async def _probe_workflow_model_access(
    spec: WorkflowSpec,
    llm: Any,
    report: WorkflowPreflightReport,
) -> None:
    """Confirm provider/project access without making a generation request."""

    before = len(report.issues)
    explicit_models: dict[str, set[str]] = defaultdict(set)
    automatic_candidates: dict[str, list[str]] = {}

    for node in spec.nodes:
        config_models = {
            model
            for _, model in _iter_model_values(
                _validated_node_config(node)
            )
        }
        automatic = (
            node.selected_model == AUTO_MODEL
            or AUTO_MODEL in config_models
        )
        if automatic:
            automatic_candidates[node.id] = [
                model
                for model in node.allowed_models
                if model != AUTO_MODEL
            ]
        for model in config_models:
            if model != AUTO_MODEL:
                explicit_models[model].add(node.id)

    models_to_probe = set(explicit_models)
    for candidates in automatic_candidates.values():
        models_to_probe.update(candidates)

    probe = getattr(llm, "probe_model_access", None)
    if probe is None:
        # Custom dependency-injected gateways own their own availability
        # contract. Structural preflight has already validated model names.
        _add_check(
            report,
            "model_access",
            before,
            "Custom LLM gateway supplied; provider metadata probe skipped.",
        )
        return

    try:
        results = await probe(models_to_probe)
    except Exception as exc:
        _issue(
            report,
            "MODEL_ACCESS_PROBE_FAILED",
            "Could not verify provider model access without generation: "
            f"{type(exc).__name__}.",
            path="services.llm",
            suggestion=(
                "Verify provider connectivity and credentials, then repeat "
                "the zero-token test."
            ),
        )
        _add_check(
            report,
            "model_access",
            before,
            "Provider model metadata could not be checked.",
        )
        return

    for model, node_ids in sorted(explicit_models.items()):
        result = results.get(model)
        if result is None:
            _issue(
                report,
                "MODEL_ACCESS_PROBE_INCOMPLETE",
                f"Model access probe returned no result for {model!r}.",
                path="services.llm",
                node_id=sorted(node_ids)[0],
                suggestion="Repeat the zero-token test before running.",
            )
            continue
        if result.available:
            continue
        _issue(
            report,
            "MODEL_ACCESS_UNAVAILABLE",
            f"Configured project cannot use model {model!r}: "
            f"{result.reason}.",
            path="services.llm",
            node_id=sorted(node_ids)[0],
            suggestion=(
                "Grant this project access, choose an accessible model, or "
                "use Auto with at least one accessible allowed model."
            ),
        )

    warned_models: set[str] = set()
    for node_id, candidates in sorted(automatic_candidates.items()):
        accessible = [
            model
            for model in candidates
            if (result := results.get(model)) is not None
            and result.available
        ]
        unavailable = [
            model
            for model in candidates
            if (result := results.get(model)) is not None
            and not result.available
        ]
        if not accessible:
            _issue(
                report,
                "AUTO_MODEL_ACCESS_UNAVAILABLE",
                "Auto has no accessible model in this node's allowed_models.",
                path=f"nodes.{node_id}.allowed_models",
                node_id=node_id,
                suggestion=(
                    "Configure provider credentials or add at least one model "
                    "the configured project can access."
                ),
            )
            continue
        for model in unavailable:
            if model in warned_models:
                continue
            warned_models.add(model)
            result = results[model]
            _issue(
                report,
                "AUTO_MODEL_CANDIDATE_EXCLUDED",
                f"Auto will exclude model {model!r}: {result.reason}.",
                severity=PreflightSeverity.WARNING,
                path="services.llm",
                suggestion=(
                    "No action is required if another accessible candidate is "
                    "acceptable; otherwise grant this project model access."
                ),
            )

    _add_check(
        report,
        "model_access",
        before,
        f"{len(models_to_probe)} model(s) checked through zero-token metadata.",
    )


async def preflight_workflow_for_run(
    yaml_text: str,
    *,
    provided_inputs: dict[str, Any] | None,
    services: dict[str, Any],
    probe_services: bool = True,
    require_run_history: bool = True,
    owner_scope_id: str | None = None,
) -> WorkflowPreflightReport:
    """Strict API gate used immediately before a new or retried run.

    `provided_inputs=None` (as opposed to `{}`) is a real, distinct case —
    forwarded as-is to `preflight_workflow_yaml`, which treats it as "skip
    input-presence validation, no real inputs exist to check yet" rather
    than "these are the real inputs, and none were given" (see
    app.api.workflow_generation's /generate, whose caller usually has no
    real inputs at all — it's producing a workflow, not running one)."""

    report = preflight_workflow_yaml(
        yaml_text,
        provided_inputs=provided_inputs,
        services=services,
        compile_graph=True,
    )
    if report.errors or not probe_services:
        return report.refresh()

    raw = _load_unique_yaml(yaml_text)
    spec = WorkflowSpec.model_validate(raw)
    await _probe_services(
        spec,
        services,
        report,
        require_run_history=require_run_history,
        owner_scope_id=owner_scope_id,
    )
    return report.refresh()


def require_preflight(report: WorkflowPreflightReport) -> None:
    if not report.valid:
        raise WorkflowPreflightError(report)
