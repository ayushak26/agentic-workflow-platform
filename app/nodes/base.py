"""Base contract for every node type in the platform.

A NodeType is a typed, schema-declared, async unit of work that the runtime
can introspect, validate, render in the UI, and execute. Subclasses declare
three pydantic schemas and one async method. That is the entire surface.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Type
from pydantic import BaseModel


class NodeType(ABC):
    # ----- declared by every subclass -----
    type_name: ClassVar[str]                    # registry key, e.g. "TransformAgent"
    input_schema: ClassVar[Type[BaseModel]]     # what this node reads from state
    output_schema: ClassVar[Type[BaseModel]]    # what this node writes back
    config_schema: ClassVar[Type[BaseModel]]    # YAML config shape, validated at load
    description: ClassVar[str] = ""             # shown in the Builder palette

    def __init__(self, node_id: str, raw_config: dict[str, Any],services: dict[str, Any] | None = None):
        # Pydantic validates the config on construction. If the YAML is wrong,
        # we fail at compile time, not in the middle of an LLM call.
        self.node_id = node_id
        self.config = self.config_schema(**raw_config)
        self.services = services or {}

    @abstractmethod
    async def run(
        self,
        state: "WorkflowState",          # forward ref to avoid circular import
        resolved_config: dict[str, Any], # config with {{...}} templates substituted
    ) -> dict[str, Any]:
        """Execute the node.

        Returns a dict that the runtime will write into
        state["node_outputs"][self.node_id]. The shape must conform to
        self.output_schema; the compiler will validate it before merging.
        """
        ...