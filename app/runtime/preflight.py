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
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field, ValidationError
import yaml

import app.nodes as nodes_package
from app.config import settings
from app.llm.catalog import local_service_name
from app.llm.model_catalog import AUTO_MODEL
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
    node_type = node.type
    config = node.effective_config()
    required: set[str] = set()

    if node_type in {
        "TransformAgent",
        "RAGAgent",
        "MCPAgent",
        "GraphNormalizer",
        "EvidenceAgent",
        "ScholarlyCandidateDiscoveryAgent",
        "ScientificSkillAgent",
        "ClaimEvidenceVerifier",
        "ProposalEvidenceFactoryAgent",
        "ConceptAlternativesAgent",
        "HorizonEvaluationAgent",
    }:
        required.update({"llm", "cost_ledger"})
    if node_type == "RouterAgent" and config.get("mode", "rule") == "llm":
        required.update({"llm", "cost_ledger"})
    if node_type == "RAGAgent":
        required.add("retriever")
    if node_type in {
        "MCPAgent",
        "EvidenceAgent",
        "ScholarlyCandidateDiscoveryAgent",
        "FullTextEvidenceAcquirer",
    }:
        required.add("mcp_client")
    if node_type == "ScientificSkillAgent":
        required.add("scientific_skill_catalog")
    if node_type in {
        "PDFTextExtractor",
        "PDFProposalRenderer",
        "HorizonHTMLProposalRenderer",
        "HorizonDOCXProposalRenderer",
        "FullTextEvidenceAcquirer",
        "ProposalEvidenceFactoryAgent",
        "DOCXProposalRenderer",
        "ExcelTableExtractor",
        "PowerPointProposalSlides",
        "WorkflowFileLoader",
    }:
        required.add("object_store")
    if (
        node_type == "HumanInLoopAgent"
        and config.get("allow_document_override", True)
    ):
        required.add("object_store")
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

        models = list(_iter_model_values(validated_config))
        if node_spec.selected_model:
            models.append((f"{path}.selected_model", node_spec.selected_model))
        for model_path, model_name in models:
            if model_name == AUTO_MODEL:
                # Routing sentinel — resolved to a concrete model at call time
                # by the deterministic ModelRouter, so it is not a catalog
                # entry and must not be routed here either.
                continue
            if model_name not in DEFAULT_LLM_MODELS:
                _issue(
                    report,
                    "MODEL_NOT_IN_CATALOG",
                    f"Model {model_name!r} is not in the approved model catalog.",
                    path=f"{path}.{model_path}",
                    node_id=node_spec.id,
                    suggestion=f"Choose one of: {', '.join(DEFAULT_LLM_MODELS)}.",
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
            rules = source.effective_config().get("rules") or []
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


def _validate_graph(
    spec: WorkflowSpec,
    report: WorkflowPreflightReport,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    before = len(report.issues)
    _validate_router_edges(spec, report)
    forward, reverse = _adjacency(spec)
    entry = spec.entry or spec.nodes[0].id
    reachable = _reachable(entry, forward)

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


def _validate_template_output_path(
    spec: WorkflowSpec,
    current_node: NodeSpec,
    reference: str,
    report: WorkflowPreflightReport,
    path: str,
    forward: dict[str, set[str]],
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

    if current_node.id not in _reachable(first, forward):
        _issue(
            report,
            "TEMPLATE_NOT_UPSTREAM",
            f"Template reads {first!r}, but that node cannot execute before "
            f"{current_node.id!r}.",
            path=path,
            node_id=current_node.id,
            suggestion="Add the required upstream edge or fix the template.",
        )

    if len(parts) < 2:
        return
    try:
        source_class = NodeRegistry.get(node_map[first].type)
    except KeyError:
        return
    fields = set(source_class.output_schema.model_fields)
    if parts[1] not in fields:
        _issue(
            report,
            "TEMPLATE_UNKNOWN_OUTPUT_FIELD",
            f"{node_map[first].type} {first!r} has no output field "
            f"{parts[1]!r}.",
            path=path,
            node_id=current_node.id,
            suggestion=f"Available fields: {', '.join(sorted(fields))}.",
        )
        return

    if (
        node_map[first].type == "TransformAgent"
        and parts[1] == "parsed"
        and len(parts) >= 3
    ):
        declared = node_map[first].effective_config().get("output_schema") or {}
        if parts[2] not in declared:
            _issue(
                report,
                "TEMPLATE_UNKNOWN_STRUCTURED_FIELD",
                f"TransformAgent {first!r} does not declare parsed field "
                f"{parts[2]!r}.",
                path=path,
                node_id=current_node.id,
                suggestion=(
                    "Add it to output_schema or correct the template path."
                ),
            )


def _validate_templates(
    spec: WorkflowSpec,
    report: WorkflowPreflightReport,
    forward: dict[str, set[str]],
) -> None:
    before = len(report.issues)
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
    forward, _ = _validate_graph(spec, report)
    _validate_templates(spec, report, forward)
    _validate_inputs(spec, provided_inputs, report)
    if compile_graph:
        _compile_dry_run(spec, services, report)
    return report.refresh()


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
            _issue(
                report,
                "OBJECT_STORE_UNAVAILABLE",
                f"Object storage is configured but not reachable: {exc}",
                path="services.object_store",
                suggestion="Start MinIO and verify endpoint/credentials.",
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
                "EvidenceAgent",
                "ScholarlyCandidateDiscoveryAgent",
                "FullTextEvidenceAcquirer",
            }:
                continue
            server = (
                node.effective_config().get("mcp_server")
                if node.type
                in {
                    "EvidenceAgent",
                    "ScholarlyCandidateDiscoveryAgent",
                    "FullTextEvidenceAcquirer",
                }
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
            if node.type in {
                "EvidenceAgent",
                "ScholarlyCandidateDiscoveryAgent",
            }:
                configured_tool = node.effective_config().get("tool")
            elif node.type == "FullTextEvidenceAcquirer":
                configured_tool = node.effective_config().get("download_tool")
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

    llm = services.get("llm")
    if "llm" in required and llm is not None:
        # Only the live registry gateway needs environment credentials. Test
        # stubs/custom local gateways are accepted by their injected contract.
        if llm.__class__.__module__ == "app.llm.registry":
            model_names = {
                model
                for node in spec.nodes
                for _, model in _iter_model_values(
                    _validated_node_config(node)
                )
            }
            for intended in sorted(model_names):
                try:
                    resolved = resolve_model(intended)
                except Exception:
                    continue
                if resolved.startswith("claude-") and not settings.anthropic_api_key:
                    _issue(
                        report,
                        "MODEL_CREDENTIAL_MISSING",
                        f"ANTHROPIC_API_KEY is missing for model {resolved!r}.",
                        suggestion="Add the key to .env.local and restart the API.",
                    )
                if resolved.startswith("gpt-") and not settings.openai_api_key:
                    _issue(
                        report,
                        "MODEL_CREDENTIAL_MISSING",
                        f"OPENAI_API_KEY is missing for model {resolved!r}.",
                        suggestion="Add the key to .env.local and restart the API.",
                    )

    _add_check(
        report,
        "required_services",
        before,
        f"{len(required)} required service(s) checked without an LLM call.",
    )


async def preflight_workflow_for_run(
    yaml_text: str,
    *,
    provided_inputs: dict[str, Any],
    services: dict[str, Any],
    probe_services: bool = True,
    require_run_history: bool = True,
) -> WorkflowPreflightReport:
    """Strict API gate used immediately before a new or retried run."""

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
    )
    return report.refresh()


def require_preflight(report: WorkflowPreflightReport) -> None:
    if not report.valid:
        raise WorkflowPreflightError(report)
