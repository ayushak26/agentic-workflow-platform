"""Global registry of node types.

The Workflow Builder reads this registry through GET /api/node-types.
The runtime compiler uses it to instantiate nodes from workflow YAML.
"""

from __future__ import annotations

from typing import Type

from pydantic import BaseModel

from app.runtime.schema import DEFAULT_LLM_MODELS

from .base import NodeType


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
        """Return the JSON schemas used by the Builder."""

        return [
            {
                "type_name": klass.type_name,
                "description": klass.description,
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
            for klass in cls._registry.values()
        ]


def _with_model_catalog(schema: dict) -> dict:
    """Expose the model catalog as a Builder dropdown."""

    model_schema = schema.get("properties", {}).get("model")

    if not isinstance(model_schema, dict):
        return schema

    if model_schema.get("type") == "string":
        model_schema["enum"] = list(DEFAULT_LLM_MODELS)
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
            variant["enum"] = list(DEFAULT_LLM_MODELS)
            break

    return schema