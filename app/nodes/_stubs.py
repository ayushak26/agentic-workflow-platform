"""Stub nodes used only to test the runtime in isolation from LLMs/RAG.
The real node implementations land in Phase 5."""
from typing import Any
from pydantic import BaseModel
from .base import NodeType
from .registry import NodeRegistry


class _Empty(BaseModel):
    """Pydantic model defining the Empty shape."""
    pass


class _LiteralConfig(BaseModel):
    """Pydantic model defining the LiteralConfig shape.

    Attributes:
        value (Any).
    """
    value: Any


class _LiteralOutput(BaseModel):
    """Pydantic model defining the LiteralOutput shape.

    Attributes:
        value (Any).
    """
    value: Any


@NodeRegistry.register
class LiteralNode(NodeType):
    """Emits whatever literal is in its config. Useful for smoke tests."""
    type_name = "Literal"
    description = "Emits a literal config value as its output."
    input_schema = _Empty
    output_schema = _LiteralOutput
    config_schema = _LiteralConfig

    async def run(self, state, resolved_config):
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config: Configuration after template resolution.
        """
        return {"value": resolved_config["value"]}


class _EchoConfig(BaseModel):
    """Pydantic model defining the EchoConfig shape.

    Attributes:
        template (str).
    """
    template: str


class _EchoOutput(BaseModel):
    """Pydantic model defining the EchoOutput shape.

    Attributes:
        text (str).
    """
    text: str


@NodeRegistry.register
class EchoNode(NodeType):
    """Renders a template string against state. Useful for testing templating."""
    type_name = "Echo"
    description = "Renders a template string."
    input_schema = _Empty
    output_schema = _EchoOutput
    config_schema = _EchoConfig

    async def run(self, state, resolved_config):
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config: Configuration after template resolution.
        """
        return {"text": resolved_config["template"]}