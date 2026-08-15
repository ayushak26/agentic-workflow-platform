"""Global registry of node types.

The Workflow Builder reads this registry through GET /api/node-types.
The runtime compiler uses it to instantiate nodes from workflow YAML.
"""

from __future__ import annotations

from typing import Type

from pydantic import BaseModel

from app.llm.model_catalog import MODEL_OPTION_LABELS, MODEL_SELECTION_OPTIONS

from .about_synthesis import synthesize_about
from .base import NodeType
from .categories import (
    category_for,
    execution_kind_for,
    family_for,
    icon_for,
)


class _EmptySchema(BaseModel):
    """Placeholder for nodes without a declared schema."""


class NodeRegistryError(KeyError):
    pass


class NodeRegistry:
    _registry: dict[str, Type[NodeType]] = {}

    @classmethod
    def register(
        cls,
        node_class: Type[NodeType],
    ) -> Type[NodeType]:
        """Register a node class."""

        if not getattr(node_class, "type_name", None):
            raise ValueError(
                f"{node_class.__name__} must declare 'type_name'"
            )

        if node_class.type_name in cls._registry:
            raise ValueError(
                f"Duplicate node type: {node_class.type_name}"
            )

        cls._registry[node_class.type_name] = node_class
        return node_class

    @classmethod
    def get(cls, type_name: str) -> Type[NodeType]:
        if type_name not in cls._registry:
            raise NodeRegistryError(
                f"Unknown node type: {type_name}"
            )

        return cls._registry[type_name]

    @classmethod
    def manifest(cls) -> list[dict]:
        """Return the JSON schemas and presentation metadata used by the Builder.

        Beyond the three schemas, each entry carries `family` (core vocabulary
        vs. specialized capability), `execution_kind` (ai / deterministic /
        external / human / input / output) and `about`. Those three are what let
        the Builder present a small mental model, make the automation boundary
        visible on the canvas, and explain a node without the author reading its
        source.
        """

        return [_manifest_entry(klass) for klass in cls._registry.values()]


def _manifest_entry(klass: Type[NodeType]) -> dict:
    uses_llm = _uses_llm(klass)
    # A class's own `about` always wins; auto-synthesized fields (derived from
    # its schemas and from real workflow adjacency — see about_synthesis.py)
    # fill in whatever it didn't declare, so every node type gets a usable
    # About tab without a second hand-authored description.
    about = {**synthesize_about(klass), **(getattr(klass, "about", {}) or {})}
    return {
        "type_name": klass.type_name,
        "description": klass.description,
        "category": category_for(klass.type_name),
        "icon": icon_for(klass.type_name),
        # A class's *own* declaration wins; the lookup tables cover the node
        # types that predate the ClassVars. `klass.__dict__` rather than
        # getattr, because NodeType declares documented defaults that every
        # subclass inherits — getattr would return the base default and the
        # table would never be consulted.
        "family": klass.__dict__.get("family") or family_for(klass.type_name),
        "execution_kind": (
            klass.__dict__.get("execution_kind")
            or execution_kind_for(klass.type_name, uses_llm=uses_llm)
        ),
        "uses_ai": bool(about.get("uses_ai", uses_llm)),
        "external_action": bool(about.get("external_action", False)),
        "about": about,
        "presets": about.get("presets") or [],
        "input_schema": getattr(
            klass,
            "input_schema",
            _EmptySchema,
        ).model_json_schema(),
        "output_schema": getattr(
            klass,
            "output_schema",
            _EmptySchema,
        ).model_json_schema(),
        "config_schema": _with_model_catalog(
            klass.config_schema.model_json_schema()
        ),
    }


def _uses_llm(klass: Type[NodeType]) -> bool:
    """Whether this node type needs a model, per its own preflight declaration.

    Read from `required_services` rather than a hand-maintained list, so it
    cannot drift: a node that starts calling a model has to declare the `llm`
    service to pass preflight anyway. Called with an empty config, so a node
    whose model use is conditional on config (RouterAgent's llm mode) reports
    its default — which is the right answer for a palette entry.
    """
    try:
        return "llm" in klass.required_services({})
    except Exception:
        return False


def _with_model_catalog(schema: dict) -> dict:
    """Expose the model catalog as a Builder dropdown."""

    model_schema = schema.get("properties", {}).get("model")

    if not isinstance(model_schema, dict):
        return schema

    if model_schema.get("type") == "string":
        model_schema["enum"] = list(MODEL_SELECTION_OPTIONS)
        model_schema["x-enum-labels"] = dict(MODEL_OPTION_LABELS)
        return schema

    variants = (
        model_schema.get("anyOf")
        or model_schema.get("oneOf")
        or []
    )

    for variant in variants:
        if (
            isinstance(variant, dict)
            and variant.get("type") == "string"
        ):
            variant["enum"] = list(MODEL_SELECTION_OPTIONS)
            variant["x-enum-labels"] = dict(MODEL_OPTION_LABELS)
            break

    return schema
