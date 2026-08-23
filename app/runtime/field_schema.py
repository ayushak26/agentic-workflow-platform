"""Visual field schema — the authoring format behind the Builder's structured
output builder.

A workflow author describes the shape they want as a flat list of ``FieldSpec``
rows (each of which may nest). This module is the single place that turns that
authoring format into the three things the platform needs:

    FieldSpec list  ──build_response_model──▶  Pydantic model  (LLM structured
                    │                                            output + validation)
                    ├──json_schema────────────▶  JSON Schema     (provider-native
                    │                                            structured output)
                    └──field_paths────────────▶  typed dotted    (mapping picker,
                                                 path index       rule editor,
                                                                 preflight)

Keeping all three derivations here is what makes the round trip honest: the
operators the rule editor offers, the paths preflight authorises, and the schema
the model is actually held to cannot drift apart, because they are computed from
the same rows.

Why not have authors write JSON Schema directly: a nullable enum inside a list
of objects is four levels of indirection in JSON Schema and one checkbox here.
The author edits rows; this module owns the indirection.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Type

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    create_model,
    field_validator,
    model_validator,
)


FieldKind = Literal[
    "string",
    "text",
    "number",
    "integer",
    "boolean",
    "enum",
    "object",
    "list",
    "date",
]

#: Kinds that carry a scalar value, i.e. cannot have `fields` children.
SCALAR_KINDS: frozenset[str] = frozenset(
    {"string", "text", "number", "integer", "boolean", "enum", "date"}
)

#: What a `list` may hold. Nested lists are deliberately excluded: they are
#: rare in business extraction schemas and every provider's structured-output
#: mode handles them worse than an object wrapper does.
ITEM_KINDS: frozenset[str] = frozenset(
    {"string", "text", "number", "integer", "boolean", "enum", "date", "object"}
)

_MAX_DEPTH = 6
_IDENTIFIER = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"


class FieldSpec(BaseModel):
    """One row in the visual schema builder.

    The same row type describes a top-level field, an object's child, and a
    list's item shape — the Builder renders one form component recursively
    rather than a different editor per depth.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: FieldKind = "string"
    description: str = ""
    required: bool = True
    # `nullable` is independent of `required`: a required-but-nullable field is
    # how you tell a model "always return this key, use null when the source
    # does not state it" — which is exactly the anti-hallucination contract for
    # extraction, and the reason REQUIRED_FIELD_MAY_BE_NULL exists in preflight
    # rather than being silently allowed.
    nullable: bool = False
    enum_values: list[str] = Field(default_factory=list)
    fields: list["FieldSpec"] = Field(default_factory=list)
    item_type: FieldKind | None = None
    item_enum_values: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_item_enum_values(cls, data: Any) -> Any:
        """Fold list-of-enum values stored under the field-level `enum_values`
        key into `item_enum_values`.

        Two independent sources produce this shape: an older saved workflow
        from before `item_enum_values` existed, and an AI-drafted suggestion
        (`/builder/assist/schema`) whose model reached for the only "allowed
        values" key it knows about instead of the list-item-specific one.
        Applied here — the single construction path every FieldSpec goes
        through, whether from YAML, a manual save, or an AI suggestion — so
        no caller has to remember to normalize it themselves. Only migrates
        when unambiguous: a list of enums with values sitting in the wrong
        key. A single enum field's `enum_values` is never touched.
        """
        if (
            isinstance(data, dict)
            and data.get("type") == "list"
            and data.get("item_type") == "enum"
            and not data.get("item_enum_values")
            and data.get("enum_values")
        ):
            data = {**data, "item_enum_values": data["enum_values"]}
        return data

    @field_validator("name")
    @classmethod
    def name_is_an_identifier(cls, value: str) -> str:
        """Compute the name is an identifier.

        Args:
            value (str): Value to process.

        Returns:
            str: The is an identifier.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("field name cannot be empty")
        if any(char not in _IDENTIFIER for char in stripped):
            raise ValueError(
                f"field name {value!r} may only contain letters, digits and _"
            )
        if stripped[0].isdigit():
            raise ValueError(f"field name {value!r} cannot start with a digit")
        return stripped

    @model_validator(mode="after")
    def shape_is_coherent(self) -> "FieldSpec":
        """Compute the shape is coherent.

        Returns:
            'FieldSpec': The is coherent.
        """
        if self.type == "enum" and not self.enum_values:
            raise ValueError(
                f"enum field {self.name!r} must declare at least one allowed value"
            )
        if self.type == "object" and not self.fields:
            raise ValueError(
                f"object field {self.name!r} must declare at least one child field"
            )
        if self.type == "list":
            if self.item_type is None:
                raise ValueError(f"list field {self.name!r} must declare item_type")
            if self.item_type not in ITEM_KINDS:
                raise ValueError(
                    f"list field {self.name!r} cannot hold {self.item_type!r} items"
                )
            if self.item_type == "enum" and not self.item_enum_values:
                raise ValueError(
                    f"list field {self.name!r} of enums must declare "
                    "item_enum_values"
                )
            if self.item_type == "object" and not self.fields:
                raise ValueError(
                    f"list field {self.name!r} of objects must declare its item "
                    "fields in `fields`"
                )
        if self.type in SCALAR_KINDS and self.fields:
            raise ValueError(
                f"{self.type} field {self.name!r} cannot have child fields"
            )
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError(
                f"field {self.name!r} has minimum greater than maximum"
            )
        return self


FieldSpec.model_rebuild()


def validate_fields(fields: list[FieldSpec], *, where: str = "output") -> None:
    """Reject duplicate names per level and runaway nesting depth.

    Both are authoring mistakes the row editor can produce but no provider
    tolerates: a duplicate key silently loses one field, and deep nesting makes
    structured-output modes fail in ways that read as model errors.
    """

    def walk(items: list[FieldSpec], path: str, depth: int) -> None:
        """Compute the walk.

        Args:
            items (list[FieldSpec]): Items to process.
            path (str): Filesystem path.
            depth (int): The depth.
        """
        if depth > _MAX_DEPTH:
            raise ValueError(
                f"{where} schema nests deeper than {_MAX_DEPTH} levels at {path}"
            )
        seen: set[str] = set()
        for item in items:
            if item.name in seen:
                raise ValueError(
                    f"{where} schema declares {item.name!r} twice under "
                    f"{path or '<root>'}"
                )
            seen.add(item.name)
            if item.fields:
                walk(item.fields, f"{path}.{item.name}" if path else item.name, depth + 1)

    walk(fields, "", 1)


# --------------------------------------------------------------------------
# Pydantic model construction
# --------------------------------------------------------------------------

_SCALAR_PY_TYPES: dict[str, Any] = {
    "string": str,
    "text": str,
    "date": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}


def _scalar_annotation(kind: str, enum_values: list[str]) -> Any:
    """Internal helper for the scalar annotation step.

    Args:
        kind (str): The kind.
        enum_values (list[str]): The enum values.

    Returns:
        Any: The annotation.
    """
    if kind == "enum":
        # Literal over the declared values, so the provider's structured-output
        # mode constrains generation to the allowed set instead of the model
        # inventing a nearby label ("tech_support" for "technical_support").
        return Literal[tuple(enum_values)]  # type: ignore[valid-type]
    return _SCALAR_PY_TYPES[kind]


def _annotation_for(
    spec: FieldSpec,
    model_name: str,
    *,
    permit_none: bool,
) -> Any:
    """Internal helper for the annotation for step.

    Args:
        spec (FieldSpec): Parsed workflow specification.
        model_name (str): The model name.
        permit_none (bool): The permit none.

    Returns:
        Any: The for.
    """
    if spec.type == "object":
        annotation: Any = _build_model(model_name, spec.fields)
    elif spec.type == "list":
        if spec.item_type == "object":
            annotation = list[_build_model(model_name, spec.fields)]  # type: ignore[misc]
        else:
            annotation = list[
                _scalar_annotation(spec.item_type or "string", spec.item_enum_values)
            ]  # type: ignore[misc]
    else:
        annotation = _scalar_annotation(spec.type, spec.enum_values)

    if spec.type in ("number", "integer") and (
        spec.minimum is not None or spec.maximum is not None
    ):
        annotation = Annotated[annotation, Field(ge=spec.minimum, le=spec.maximum)]

    if permit_none:
        annotation = annotation | None

    return annotation


def _build_model(name: str, fields: list[FieldSpec]) -> Type[BaseModel]:
    """Build the model.

    Args:
        name (str): Workflow or resource name.
        fields (list[FieldSpec]): Field names.

    Returns:
        Type[BaseModel]: The model.
    """
    definitions: dict[str, Any] = {}
    for spec in fields:
        # An optional field is compiled as nullable-with-a-None-default (lists
        # excepted, which default to empty). Pydantic cannot express "may be
        # absent but never null" in a form any structured-output provider
        # honours, and once a response is parsed a missing key and an explicit
        # null are the same thing to every downstream reader.
        optional = not spec.required
        permit_none = spec.nullable or (optional and spec.type != "list")
        annotation = _annotation_for(
            spec, f"{name}_{spec.name}", permit_none=permit_none
        )
        if spec.required:
            default: Any = ...
        elif spec.type == "list":
            default = []
        else:
            default = None
        definitions[spec.name] = (
            annotation,
            Field(default, description=spec.description or None),
        )
    return create_model(  # type: ignore[call-overload]
        _safe_model_name(name),
        __config__=ConfigDict(extra="ignore"),
        **definitions,
    )


def _safe_model_name(name: str) -> str:
    """Internal helper for the safe model name step.

    Args:
        name (str): Workflow or resource name.

    Returns:
        str: The model name.
    """
    cleaned = "".join(char if char in _IDENTIFIER else "_" for char in name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"F{cleaned}"
    return cleaned


def build_response_model(
    fields: list[FieldSpec],
    *,
    model_name: str = "StructuredOutput",
) -> Type[BaseModel]:
    """Compile the visual schema into a Pydantic model."""
    validate_fields(fields)
    return _build_model(model_name, fields)


def json_schema(fields: list[FieldSpec], *, model_name: str = "StructuredOutput") -> dict[str, Any]:
    """JSON Schema for the visual schema, via the compiled Pydantic model.

    Derived from the model rather than emitted independently, so the schema the
    provider is given and the schema the response is validated against are the
    same object by construction.
    """
    return build_response_model(fields, model_name=model_name).model_json_schema()


# --------------------------------------------------------------------------
# Typed path index — what the mapping picker, rule editor and preflight read
# --------------------------------------------------------------------------

#: A path's type as seen by the *index*, not by the authoring form. "unknown" is
#: how the preflight/Builder index represents a value coming out of a node whose
#: output is a free-form dict rather than a visual schema: the path is real, but
#: nothing can be proved about its type, so the rule editor offers every operator
#: and preflight stays silent instead of guessing.
IndexedFieldKind = FieldKind | Literal["unknown"]


class FieldPath(BaseModel):
    """One addressable value inside a compiled schema."""

    model_config = ConfigDict(extra="forbid")

    path: str
    type: IndexedFieldKind
    description: str = ""
    required: bool = True
    nullable: bool = False
    enum_values: list[str] = Field(default_factory=list)
    #: For `list` paths: what the list holds, so the rule editor can offer
    #: `contains <enum value>` rather than `contains <free text>`.
    item_type: IndexedFieldKind | None = None
    #: True when any ancestor is optional/nullable — the value can be
    #: unavailable even if this field itself is required, which is what
    #: §15's "indicate whether the value can be unavailable" needs.
    may_be_unavailable: bool = False


def field_paths(fields: list[FieldSpec], prefix: str = "") -> list[FieldPath]:
    """Flatten a visual schema into typed dotted paths, parents included.

    Parents are included because they are legitimate mapping targets (pass a
    whole `customer` object to a downstream node) and legitimate rule subjects
    (`customer exists`).
    """
    return _field_paths(fields, prefix, parent_unavailable=False)


def _field_paths(
    fields: list[FieldSpec],
    prefix: str,
    *,
    parent_unavailable: bool,
) -> list[FieldPath]:
    """Internal helper for the field paths step.

    Args:
        fields (list[FieldSpec]): Field names.
        prefix (str): Prefix string.
        parent_unavailable (bool): The parent unavailable.

    Returns:
        list[FieldPath]: The paths.
    """
    result: list[FieldPath] = []
    for spec in fields:
        path = f"{prefix}.{spec.name}" if prefix else spec.name
        unavailable = parent_unavailable or not spec.required or spec.nullable
        result.append(
            FieldPath(
                path=path,
                type=spec.type,
                description=spec.description,
                required=spec.required,
                nullable=spec.nullable,
                enum_values=(
                    spec.enum_values
                    if spec.type == "enum"
                    else spec.item_enum_values
                    if spec.item_type == "enum"
                    else []
                ),
                item_type=spec.item_type,
                may_be_unavailable=unavailable,
            )
        )
        if spec.type == "object" and spec.fields:
            result.extend(
                _field_paths(spec.fields, path, parent_unavailable=unavailable)
            )
        elif spec.type == "list" and spec.item_type == "object" and spec.fields:
            # A list of objects exposes its item fields as `items.<name>` — a
            # rule can then ask "does any item's model exist", and the mapping
            # picker can show the item shape instead of an opaque list.
            result.extend(
                _field_paths(
                    spec.fields,
                    f"{path}.items",
                    parent_unavailable=True,
                )
            )
    return result


def parse_fields(raw: Any) -> list[FieldSpec]:
    """Coerce YAML/JSON rows into FieldSpec, with a clear error on bad shape."""
    if raw in (None, "", [], {}):
        return []
    if not isinstance(raw, list):
        raise ValueError("output_fields must be a list of field definitions")
    return [
        item if isinstance(item, FieldSpec) else FieldSpec.model_validate(item)
        for item in raw
    ]


def describe_schema(fields: list[FieldSpec]) -> str:
    """A compact, human-readable contract used in prompts and the About tab.

    Sent to the model *alongside* provider-native structured output, not instead
    of it: the schema constrains shape, this explains intent (which is what
    stops a model filling `serial_number` with the order number).
    """
    lines: list[str] = []

    def walk(items: list[FieldSpec], indent: int) -> None:
        """Compute the walk.

        Args:
            items (list[FieldSpec]): Items to process.
            indent (int): The indent.
        """
        pad = "  " * indent
        for spec in items:
            bits = [spec.type]
            if spec.type == "enum":
                bits.append("one of: " + ", ".join(spec.enum_values))
            if spec.type == "list":
                bits.append(f"of {spec.item_type}")
                if spec.item_enum_values:
                    bits.append("one of: " + ", ".join(spec.item_enum_values))
            bits.append("required" if spec.required else "optional")
            if spec.nullable:
                bits.append("null when not stated")
            suffix = f" — {spec.description}" if spec.description else ""
            lines.append(f"{pad}- {spec.name} ({'; '.join(bits)}){suffix}")
            if spec.fields:
                walk(spec.fields, indent + 1)

    walk(fields, 0)
    return "\n".join(lines)
