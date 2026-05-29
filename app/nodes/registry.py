"""Global registry of node types.

The Workflow Builder UI reads this via GET /api/node-types to populate the
node palette and auto-generate config forms. The runtime compiler reads
this to instantiate nodes from YAML 'type' fields.

A single source of truth — there are no other places that know what nodes
exist."""
from __future__ import annotations
from typing import Type
from .base import NodeType


class NodeRegistryError(KeyError):
    pass


class NodeRegistry:
    _registry: dict[str, Type[NodeType]] = {}

    @classmethod
    def register(cls, node_class: Type[NodeType]) -> Type[NodeType]:
        """Decorator: @NodeRegistry.register on every node class."""
        if not getattr(node_class, "type_name", None):
            raise ValueError(f"{node_class.__name__} must declare 'type_name'")
        if node_class.type_name in cls._registry:
            raise ValueError(f"Duplicate node type: {node_class.type_name}")
        cls._registry[node_class.type_name] = node_class
        return node_class

    @classmethod
    def get(cls, type_name: str) -> Type[NodeType]:
        if type_name not in cls._registry:
            raise NodeRegistryError(f"Unknown node type: {type_name}")
        return cls._registry[type_name]

    @classmethod
    def manifest(cls) -> list[dict]:
        """JSON-schema dump used by the Builder UI."""
        return [
            {
                "type_name": klass.type_name,
                "description": klass.description,
                "input_schema": klass.input_schema.model_json_schema(),
                "output_schema": klass.output_schema.model_json_schema(),
                "config_schema": klass.config_schema.model_json_schema(),
            }
            for klass in cls._registry.values()
        ]