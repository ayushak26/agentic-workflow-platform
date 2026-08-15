"""Fills in `about` metadata for node types that don't declare their own.

Only the 8 "core" node types hand-author an `about` dict (see app/nodes/base.py).
The other ~40 specialized types would otherwise show nothing in the Builder's
About tab beyond their one-line `description`. Rather than hand-writing a
second description for every one of them (which would drift the moment the
node's real behaviour changes), this module derives what it can from data the
registry already has: the node's own schemas, and how it is actually wired up
in the workflows that already exist on disk.

Nothing here calls an LLM or invents a fact — every derived field is either a
schema field name, a real adjacency mined from a real workflow file, or an
explicitly generic template sentence. `NodeRegistry._manifest_entry` merges
this under a class's own `about`, so an explicit declaration always wins and
a brand-new node type gets *something* useful for free.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Type, TYPE_CHECKING

import yaml

from .categories import category_for, family_for

if TYPE_CHECKING:
    from .base import NodeType

_WORKFLOW_GLOBS = (
    "workflows/*.yaml",
    "workflows/collections/*.yaml",
    "workflows/pipelines/*.yaml",
    "workflows/test_fixtures/*.yaml",
    "workflows/test_fixtures/**/*.yaml",
)

_MAX_NEIGHBOURS = 3


def _repo_root() -> Path:
    # app/nodes/about_synthesis.py -> app/nodes -> app -> repo root
    return Path(__file__).resolve().parents[2]


def _edge_targets(edge: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    to = edge.get("to")
    if isinstance(to, str):
        targets.append(to)
    elif isinstance(to, list):
        targets.extend(t for t in to if isinstance(t, str))
    branches = edge.get("branches")
    if isinstance(branches, dict):
        targets.extend(t for t in branches.values() if isinstance(t, str))
    return targets


@lru_cache(maxsize=1)
def _adjacency() -> dict[str, dict[str, Any]]:
    """type_name -> {"upstream": [...], "downstream": [...], "example": str|None},
    mined once from every workflow YAML on disk. Neighbours are ranked by how
    often they co-occur, most common first. An unreadable or missing
    workflows/ directory just yields an empty mapping — this is presentation
    sugar, never load-bearing."""
    upstream_counts: dict[str, dict[str, int]] = {}
    downstream_counts: dict[str, dict[str, int]] = {}
    examples: dict[str, str] = {}

    root = _repo_root()
    seen_paths: set[Path] = set()
    for pattern in _WORKFLOW_GLOBS:
        try:
            paths = list(root.glob(pattern))
        except OSError:
            continue
        for path in paths:
            if path in seen_paths or not path.is_file():
                continue
            seen_paths.add(path)
            try:
                doc = yaml.safe_load(path.read_text())
            except Exception:
                continue
            if not isinstance(doc, dict):
                continue
            nodes = doc.get("nodes") or []
            node_type_by_id: dict[str, str] = {}
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_id, node_type = node.get("id"), node.get("type")
                if isinstance(node_id, str) and isinstance(node_type, str):
                    node_type_by_id[node_id] = node_type
            for node_type in node_type_by_id.values():
                examples.setdefault(node_type, path.relative_to(root).as_posix())
            for edge in doc.get("edges") or []:
                if not isinstance(edge, dict):
                    continue
                src_type = node_type_by_id.get(edge.get("from_") or edge.get("from"))
                if not src_type:
                    continue
                for dst_id in _edge_targets(edge):
                    dst_type = node_type_by_id.get(dst_id)
                    if not dst_type:
                        continue
                    downstream_counts.setdefault(src_type, {})
                    downstream_counts[src_type][dst_type] = downstream_counts[src_type].get(dst_type, 0) + 1
                    upstream_counts.setdefault(dst_type, {})
                    upstream_counts[dst_type][src_type] = upstream_counts[dst_type].get(src_type, 0) + 1

    def _ranked(counts: dict[str, int]) -> list[str]:
        return [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:_MAX_NEIGHBOURS]

    result: dict[str, dict[str, Any]] = {}
    for type_name in set(upstream_counts) | set(downstream_counts) | set(examples):
        result[type_name] = {
            "upstream": _ranked(upstream_counts.get(type_name, {})),
            "downstream": _ranked(downstream_counts.get(type_name, {})),
            "example": examples.get(type_name),
        }
    return result


def _schema_fields_summary(schema: Type[Any] | None) -> str:
    if schema is None:
        return ""
    fields = getattr(schema, "model_fields", {}) or {}
    parts = []
    for name, info in fields.items():
        desc = getattr(info, "description", None)
        parts.append(f"{name} ({desc})" if desc else name)
    return ", ".join(parts)


def _important_config_fields(config_schema: Type[Any] | None) -> list[str]:
    if config_schema is None:
        return []
    fields = getattr(config_schema, "model_fields", {}) or {}
    important = [name for name, info in fields.items() if getattr(info, "is_required", lambda: False)()]
    if important:
        return important
    # No required fields — surface the ones with an authored description
    # instead, since those are the ones an author actually has to decide about.
    return [name for name, info in fields.items() if getattr(info, "description", None)]


def synthesize_about(klass: "Type[NodeType]") -> dict[str, Any]:
    """Best-effort `about` dict for a node type that didn't author its own.
    Every value here is derived, never guessed at the level of a specific
    fact — see module docstring."""
    type_name = klass.type_name
    category = category_for(type_name)
    family = klass.__dict__.get("family") or family_for(type_name)
    description = klass.description or f"{type_name} node."
    neighbours = _adjacency().get(type_name, {})

    about: dict[str, Any] = {
        "receives": _schema_fields_summary(getattr(klass, "input_schema", None)) or None,
        "produces": _schema_fields_summary(getattr(klass, "output_schema", None)) or None,
        "important_config": _important_config_fields(getattr(klass, "config_schema", None)),
        "when_to_use": (
            f"When your workflow needs exactly this capability: {description}"
        ),
        "when_not_to_use": (
            "Skip it if one of the Core Building Blocks (AI Task, Decision, "
            "Router, Transform) already covers the need — "
            f"{type_name} is a specialized {category} capability, not a "
            "general-purpose one."
            if family != "core" else
            "This is a core building block — most workflows use it somewhere; "
            "reach for a specialized node only when this one's configuration "
            "genuinely can't express what you need."
        ),
        "typical_upstream": neighbours.get("upstream", []),
        "typical_downstream": neighbours.get("downstream", []),
        "example": (
            f"See {neighbours['example']} for a working example."
            if neighbours.get("example") else None
        ),
        # The raw path behind `example`, kept separate from that human-readable
        # sentence so callers that need the real file (not prose about it) —
        # e.g. app.api.workflow_generation's "real usage example" step — don't
        # have to parse it back out of a sentence.
        "example_workflow_path": neighbours.get("example"),
    }
    return {key: value for key, value in about.items() if value not in (None, [], "")}
