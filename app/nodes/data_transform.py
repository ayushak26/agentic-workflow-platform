"""DataTransformAgent — deterministic data shaping. No model call.

Renaming a field, picking three values out of twenty, normalising a unit,
building an object for an API payload: none of that needs a language model, and
using one makes a reliable operation unreliable and expensive.

(The pre-existing ``TransformAgent`` is an *LLM* transform — its name predates
this split. It stays registered and unchanged for the workflows that use it;
this node is the deterministic counterpart, which is a genuine capability
boundary rather than a different prompt.)

Every operation writes one target field under ``data``, so a node's whole output
contract is readable straight off its operation list.
"""
from __future__ import annotations

import json
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.runtime.rules import resolve_path


TransformOperationKind = Literal[
    "copy",
    "constant",
    "format",
    "join",
    "coalesce",
    "object",
    "select",
    "number",
    "boolean",
    "lowercase",
    "uppercase",
    "trim",
    "count",
    "split",
]


class TransformOperation(BaseModel):
    """One deterministic assignment into the output object."""

    model_config = ConfigDict(extra="forbid")

    target: str
    operation: TransformOperationKind = "copy"
    #: Source path for path-reading operations (copy, number, count, …).
    source: str | None = None
    #: Multiple sources for `coalesce` (first non-empty wins) and `join`.
    sources: list[str] = Field(default_factory=list)
    #: Literal for `constant`, template for `format`, separator for
    #: `join`/`split`, field list for `select`, key→path map for `object`.
    value: Any = None
    #: Unit conversion factor applied by `number`, e.g. 1000 for m³/h → l/h.
    multiply_by: float | None = None
    #: Value used when the source is missing or empty.
    default: Any = None
    description: str = ""

    @model_validator(mode="after")
    def operation_has_its_inputs(self) -> "TransformOperation":
        """Compute the operation has its inputs.

        Returns:
            'TransformOperation': The has its inputs.
        """
        needs_source = {
            "copy",
            "select",
            "number",
            "boolean",
            "lowercase",
            "uppercase",
            "trim",
            "count",
            "split",
        }
        if self.operation in needs_source and not self.source:
            raise ValueError(
                f"operation {self.operation!r} on {self.target!r} needs a source"
            )
        if self.operation == "coalesce" and not self.sources:
            raise ValueError(
                f"coalesce on {self.target!r} needs at least one source path"
            )
        if self.operation == "join" and not self.sources:
            raise ValueError(f"join on {self.target!r} needs source paths")
        if self.operation == "format" and not isinstance(self.value, str):
            raise ValueError(
                f"format on {self.target!r} needs a template string in `value`"
            )
        if self.operation == "object" and not isinstance(self.value, dict):
            raise ValueError(
                f"object on {self.target!r} needs a key → source-path map in "
                "`value`"
            )
        if self.operation == "select" and not isinstance(self.value, list):
            raise ValueError(
                f"select on {self.target!r} needs a list of field names in "
                "`value`"
            )
        if self.operation == "constant" and self.value is None:
            raise ValueError(f"constant on {self.target!r} needs a value")
        return self


class DataTransformConfig(BaseModel):
    """Pydantic model defining the DataTransformConfig shape.

    Attributes:
        operations (list[TransformOperation]).
        omit_empty (bool).
    """
    model_config = ConfigDict(extra="forbid")

    operations: list[TransformOperation] = Field(
        default_factory=list,
        description="Deterministic operations (copy, rename, format, join, coalesce, unit conversion) that build the output object.",
    )
    #: Drop keys whose computed value is None. Useful when building a payload
    #: for an API that rejects explicit nulls.
    omit_empty: bool = Field(
        default=False,
        description="Drop keys whose computed value is empty/None — useful when building a payload for an API that rejects explicit nulls.",
    )

    @model_validator(mode="after")
    def targets_are_unique(self) -> "DataTransformConfig":
        """Compute the targets are unique.

        Returns:
            'DataTransformConfig': The are unique.
        """
        targets = [op.target for op in self.operations]
        duplicates = sorted({name for name in targets if targets.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"transform writes {duplicates} more than once; each target "
                "must be assigned by exactly one operation"
            )
        return self


class DataTransformInput(BaseModel):
    """Pydantic model defining the DataTransformInput shape."""
    pass


class DataTransformOutput(BaseModel):
    """Pydantic model defining the DataTransformOutput shape.

    Attributes:
        data (dict[str, Any]).
        defaulted (list[str]).
    """
    data: dict[str, Any] = Field(default_factory=dict)
    #: Targets whose source was missing or empty and which fell back to their
    #: default. Surfaced rather than silent, because a mapping that quietly
    #: produces nulls is the hardest kind of workflow bug to see.
    defaulted: list[str] = Field(default_factory=list)


_TEMPLATE = re.compile(r"\{\{\s*([\w\.]+)\s*\}\}")


@NodeRegistry.register
class DataTransformAgent(NodeType):
    """Workflow node type implementing the DataTransformAgent capability.

    Attributes:
        family (ClassVar[str]).
        execution_kind (ClassVar[str]).
        about (ClassVar[dict[str, Any]]).
    """
    type_name = "DataTransformAgent"
    description = (
        "Deprecated — use TransformAgent's mode: deterministic instead. "
        "Deterministic data shaping: rename, select, merge, format, normalise "
        "units, build objects. No model call."
    )
    input_schema = DataTransformInput
    output_schema = DataTransformOutput
    config_schema = DataTransformConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "deterministic"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Deprecated. TransformAgent now covers the same deterministic "
            "operations under mode: deterministic, converging with its AI "
            "mode on one config/output schema — use that for any new step. "
            "Builds a new object from upstream values using deterministic "
            "operations — copy, rename, format, join, coalesce, unit conversion."
        ),
        "why": (
            "These operations are exact. Using a model for them would add cost, "
            "latency and a failure mode for no benefit."
        ),
        "receives": "Any upstream values, addressed by path.",
        "produces": "data.<target> for each configured operation.",
        "uses_ai": False,
        "external_action": False,
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return set()

    @classmethod
    def preflight_output_fields(cls, config: dict[str, Any]) -> set[str]:
        """Compute the preflight output fields.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The output fields.
        """
        targets = {
            op.get("target")
            for op in (config.get("operations") or [])
            if isinstance(op, dict) and op.get("target")
        }
        return {"data", "defaulted"} | {f"data.{name}" for name in targets}

    @classmethod
    def preflight_static_output_values(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Compute the preflight static output values.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            dict[str, Any]: The static output values.
        """
        if not (config.get("operations") or []):
            return {"data": {}}
        return {}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = DataTransformConfig(**resolved_config)
        context = dict(state)
        data: dict[str, Any] = {}
        defaulted: list[str] = []

        for op in cfg.operations:
            value = _apply(op, context)
            if _is_blank(value) and op.default is not None:
                value = op.default
                defaulted.append(op.target)
            if cfg.omit_empty and value is None:
                continue
            data[op.target] = value

        return {"data": data, "defaulted": defaulted}


def _is_blank(value: Any) -> bool:
    """Return whether blank.

    Args:
        value (Any): Value to process.

    Returns:
        bool: True when blank.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _apply(op: TransformOperation, context: dict[str, Any]) -> Any:
    """Apply the result.

    Args:
        op (TransformOperation): The op.
        context (dict[str, Any]): The context.

    Returns:
        Any: The result.
    """
    kind = op.operation
    if kind == "constant":
        return op.value
    if kind == "copy":
        return resolve_path(context, op.source or "")
    if kind == "format":
        return _TEMPLATE.sub(
            lambda match: _as_text(resolve_path(context, match.group(1))),
            str(op.value),
        )
    if kind == "join":
        separator = op.value if isinstance(op.value, str) else ", "
        parts = [
            _as_text(resolve_path(context, path))
            for path in op.sources
        ]
        return separator.join(part for part in parts if part)
    if kind == "coalesce":
        for path in op.sources:
            candidate = resolve_path(context, path)
            if not _is_blank(candidate):
                return candidate
        return None
    if kind == "object":
        built: dict[str, Any] = {}
        for key, entry in (op.value or {}).items():
            # `$path` reads a live value; anything else is a literal. Ordinary
            # strings stay literal because the runtime already substituted any
            # {{...}} references in this config before run() was called — so an
            # author who wants a mapped value writes the template they use
            # everywhere else, and `$path` exists for the node-test/simulation
            # paths where config is evaluated without templating.
            built[key] = (
                resolve_path(context, entry[1:])
                if isinstance(entry, str) and entry.startswith("$")
                else entry
            )
        return built
    if kind == "select":
        source = resolve_path(context, op.source or "")
        if not isinstance(source, dict):
            return {}
        return {key: source[key] for key in (op.value or []) if key in source}
    if kind == "number":
        number = _as_number(resolve_path(context, op.source or ""))
        if number is None:
            return None
        if op.multiply_by is not None:
            number *= op.multiply_by
        return int(number) if float(number).is_integer() else number
    if kind == "boolean":
        return _as_boolean(resolve_path(context, op.source or ""))
    if kind == "count":
        value = resolve_path(context, op.source or "")
        if isinstance(value, (list, tuple, set, dict, str)):
            return len(value)
        return 0
    if kind == "split":
        separator = op.value if isinstance(op.value, str) else ","
        text = _as_text(resolve_path(context, op.source or ""))
        return [part.strip() for part in text.split(separator) if part.strip()]

    text = _as_text(resolve_path(context, op.source or ""))
    if kind == "lowercase":
        return text.lower()
    if kind == "uppercase":
        return text.upper()
    return text.strip()


def _as_text(value: Any) -> str:
    """Internal helper for the as text step.

    Args:
        value (Any): Value to process.

    Returns:
        str: The text.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def _as_number(value: Any) -> float | None:
    """Internal helper for the as number step.

    Args:
        value (Any): Value to process.

    Returns:
        float | None: The number.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # Tolerate the way numbers actually arrive from extraction: "15 m³/h",
        # "1.234,5", "approx. 20".
        match = re.search(r"-?\d+(?:[.,]\d+)?", value.replace(" ", ""))
        if match:
            try:
                return float(match.group(0).replace(",", "."))
            except ValueError:
                return None
    return None


def _as_boolean(value: Any) -> bool:
    """Internal helper for the as boolean step.

    Args:
        value (Any): Value to process.

    Returns:
        bool: The boolean.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "y", "1", "ja", "oui")
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return False
