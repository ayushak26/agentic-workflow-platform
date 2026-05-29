from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field, ConfigDict


class WorkflowInputSpec(BaseModel):
    type: str  # "file" | "text" | "json"
    description: str | None = None


class NodeSpec(BaseModel):
    id: str
    type: str                  # NodeRegistry key
    config: dict[str, Any] = Field(default_factory=dict)

class StaticVariable(BaseModel):
    name: str
    type: str
    value: Any

class WorkflowOutputNode(BaseModel):
    node_id: str
    flatten: bool = True

class WorkflowOutputSpec(BaseModel):
    include_input: bool = False
    nodes: list[WorkflowOutputNode] = Field(default_factory=list)

class WorkflowSpec(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0"
    inputs: dict[str, WorkflowInputSpec] = Field(default_factory=dict)
    static_variables: list[StaticVariable] = Field(default_factory=list)  # NEW
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
    entry: str | None = None
    exit: str | list[str] | None = None
    output: WorkflowOutputSpec | None = None  # NEW

class EdgeSpec(BaseModel):
    """Edges support three modes:
    - simple: from='a', to='b'
    - fan-out: from='a', to=['b','c','d']  → 'a' triggers all three
    - conditional: from='a', condition='outputs.route', branches={'yes':'b','no':'c'}
    """
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str | list[str] | None = None
    condition: str | None = None
    branches: dict[str, str] | None = None


class WorkflowSpec(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0"
    inputs: dict[str, WorkflowInputSpec] = Field(default_factory=dict)
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
    entry: str | None = None   # defaults to first node if omitted
    exit: str | list[str] | None = None  # defaults to last node