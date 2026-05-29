"""Stub nodes used only to test the runtime in isolation from LLMs/RAG.
The real node implementations land in Phase 5."""
from typing import Any
from pydantic import BaseModel
from .base import NodeType
from .registry import NodeRegistry


class _Empty(BaseModel):
    pass


class _LiteralConfig(BaseModel):
    value: Any


class _LiteralOutput(BaseModel):
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
        return {"value": resolved_config["value"]}


class _EchoConfig(BaseModel):
    template: str


class _EchoOutput(BaseModel):
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
        return {"text": resolved_config["template"]}