"""Versioned, capability-based contracts shared by every workflow node type.

The workflow runtime uses a shared state object, so an edge represents ordering
and upstream visibility rather than a single positional payload.  ``state`` is
therefore the universal transport capability.  More specific data types are
derived from Pydantic schemas and allow future/direct-payload nodes to be
checked without introducing source/target pair tables.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataType(str, Enum):
    """Closed vocabulary used by generic edge compatibility policies."""

    STATE = "state"
    TEXT = "text"
    JSON = "json"
    NUMBER = "number"
    BOOLEAN = "boolean"
    FILE = "file"
    IMAGE = "image"
    TABLE = "table"
    LIST = "list"
    ANY = "any"


class NodeConstraint(BaseModel):
    """One named, reusable compatibility policy attached declaratively."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class NodeDefinition(BaseModel):
    """Complete generic contract for one registered node type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "1"
    type_name: str = Field(min_length=1)
    accepts: frozenset[DataType]
    produces: frozenset[DataType]
    requires_capabilities: frozenset[str] = frozenset()
    provides_capabilities: frozenset[str] = frozenset()
    execution_kind: Literal[
        "ai", "deterministic", "external", "human", "input", "output"
    ] = "deterministic"
    streaming: bool = False
    async_safe: bool = True
    supports_files: bool = False
    max_payload_bytes: int | None = Field(default=None, gt=0)
    permissions: frozenset[str] = frozenset()
    environment: frozenset[str] = frozenset()
    constraints: tuple[NodeConstraint, ...] = ()
    incompatible_with: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def validate_contract(self) -> "NodeDefinition":
        """Reject metadata gaps and contradictory declarations early."""

        if not self.accepts:
            raise ValueError("accepts must contain at least one data type")
        if not self.produces:
            raise ValueError("produces must contain at least one data type")
        if DataType.FILE in self.accepts | self.produces and not self.supports_files:
            raise ValueError("file data requires supports_files=true")
        if any(not item.strip() for item in self.requires_capabilities):
            raise ValueError("required capability names cannot be blank")
        return self


class CompatibilityIssue(BaseModel):
    """Stable, structured reason an edge is invalid or needs attention."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    policy: str


class CompatibilityResult(BaseModel):
    """Result of applying every universal policy to one directed edge."""

    compatible: bool
    shared_types: frozenset[DataType] = frozenset()
    issues: tuple[CompatibilityIssue, ...] = ()


def data_types_from_schema(schema: type[BaseModel]) -> frozenset[DataType]:
    """Derive broad transport types from a node's existing Pydantic schema."""

    found: set[DataType] = {DataType.STATE}
    json_schema = schema.model_json_schema()
    for field in json_schema.get("properties", {}).values():
        _collect_json_types(field, found)
    return frozenset(found)


def _collect_json_types(field: dict[str, Any], found: set[DataType]) -> None:
    variants: Iterable[dict[str, Any]] = field.get("anyOf") or field.get("oneOf") or (field,)
    for variant in variants:
        kind = variant.get("type")
        if kind == "string":
            found.add(DataType.TEXT)
        elif kind in {"integer", "number"}:
            found.add(DataType.NUMBER)
        elif kind == "boolean":
            found.add(DataType.BOOLEAN)
        elif kind == "array":
            found.add(DataType.LIST)
        elif kind == "object":
            found.add(DataType.JSON)
        title = str(variant.get("title", "")).lower()
        if "file" in title:
            found.add(DataType.FILE)
        if "image" in title:
            found.add(DataType.IMAGE)


def check_compatibility(
    source: NodeDefinition,
    target: NodeDefinition,
    *,
    available_capabilities: set[str] | None = None,
    available_permissions: set[str] | None = None,
) -> CompatibilityResult:
    """Apply universal policies; never branches on a source/target pair."""

    issues: list[CompatibilityIssue] = []
    shared = set(source.produces & target.accepts)
    if DataType.ANY in source.produces or DataType.ANY in target.accepts:
        shared.add(DataType.ANY)
    if not shared:
        issues.append(CompatibilityIssue(
            code="EDGE_DATA_TYPE_INCOMPATIBLE",
            policy="data_type_intersection",
            message=(
                f"{source.type_name} produces {sorted(x.value for x in source.produces)}, "
                f"but {target.type_name} accepts {sorted(x.value for x in target.accepts)}."
            ),
        ))
    if target.type_name in source.incompatible_with or source.type_name in target.incompatible_with:
        issues.append(CompatibilityIssue(
            code="EDGE_EXPLICITLY_INCOMPATIBLE",
            policy="explicit_restrictions",
            message=f"The contracts explicitly prohibit {source.type_name} → {target.type_name}.",
        ))
    if available_capabilities is not None:
        missing = set(target.requires_capabilities) - available_capabilities
        if missing:
            issues.append(CompatibilityIssue(
                code="EDGE_CAPABILITY_UNAVAILABLE",
                policy="required_capabilities",
                message=f"{target.type_name} requires unavailable capabilities: {sorted(missing)}.",
            ))
    if available_permissions is not None:
        missing = set(target.permissions) - available_permissions
        if missing:
            issues.append(CompatibilityIssue(
                code="EDGE_PERMISSION_UNAVAILABLE",
                policy="permissions",
                message=f"The execution context lacks permissions required by {target.type_name}: {sorted(missing)}.",
            ))
    return CompatibilityResult(
        compatible=not any(issue.severity == "error" for issue in issues),
        shared_types=frozenset(shared),
        issues=tuple(issues),
    )